"""Tableau de bord web (Streamlit) pour explorer la base de connaissances.

Interface locale complementaire a la livraison Telegram : elle permet de
consulter visuellement tout ce que l'agent a collecte et enrichi.

Lancement :
    streamlit run src/research_agent/dashboard.py

Onglets :
- Articles  : liste filtrable (source / theme / statut) + detail (score, resume, lien) ;
- Recherche : recherche plein texte (FTS5) dans les articles ;
- Runs      : historique des executions (metriques, cout LLM).
"""

from __future__ import annotations

import json

import streamlit as st

from research_agent.config import load_settings
from research_agent.deep_research import deep_analyze
from research_agent.llm.client import LLMClient
from research_agent.models import Theme
from research_agent.storage.database import Database
from research_agent.storage.search import search_items


def _save_deep(db, item_id, analysis):
    """Persiste le resultat de la deep research dans la metadata de l'item."""
    with db.connection() as conn:
        row = conn.execute("SELECT metadata FROM items WHERE id = ?;", (item_id,)).fetchone()
        if row is None:
            return
        try:
            meta = json.loads(row["metadata"] or "{}")
        except (ValueError, TypeError):
            meta = {}
        meta["deep_research"] = analysis
        conn.execute("UPDATE items SET metadata = ? WHERE id = ?;",
                     (json.dumps(meta, ensure_ascii=False), item_id))


def _db() -> Database:
    return Database.from_config(load_settings().app.database)


def _load_items(db, source=None, theme=None, status=None, limit=500):
    clauses, params = [], []
    if source and source != "toutes":
        clauses.append("source = ?"); params.append(source)
    if theme and theme != "tous":
        clauses.append("theme = ?"); params.append(theme)
    if status and status != "tous":
        clauses.append("status = ?"); params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    sql = f"SELECT * FROM items {where} ORDER BY relevance DESC NULLS LAST LIMIT ?;"
    with db.connection() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _score(row) -> int:
    try:
        meta = json.loads(row.get("metadata") or "{}")
    except (ValueError, TypeError):
        meta = {}
    s = meta.get("llm_score")
    if s is None and row.get("relevance") is not None:
        s = round(float(row["relevance"]) * 100)
    return int(s or 0)


def _summary(row):
    try:
        meta = json.loads(row.get("metadata") or "{}")
    except (ValueError, TypeError):
        meta = {}
    return meta.get("summary") or []


def _freshness(row):
    """Retourne (badge, libelle) sur la fraicheur de l'article a partir de sa date."""
    from datetime import datetime

    raw = row.get("published_at")
    if not raw:
        return "⚪", "date inconnue"
    try:
        # Dates ISO (avec ou sans heure/fuseau).
        d = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return "⚪", str(raw)
    days = (datetime.utcnow() - d).days
    if days < 0:
        days = 0
    if days <= 7:
        return "🟢", f"récent ({days} j)"
    if days <= 30:
        return "🟡", f"{days} jours"
    return "🔴", f"ancien ({days} j)"


# Categories metier (basees sur la source, plus fiable que le theme LLM).
# tenders officiels -> Appels d'offres ; actualite -> Prospects ; justice -> Juridique.
_CATEGORY_BY_SOURCE = {
    "tenderned": "tenders",
    "ted": "tenders",
    "google_news": "prospects",
    "aedes": "secteur",
    "rechtspraak": "juridique",
    "openalex": "secteur",
}

_CATEGORY_LABELS = {
    "tenders": "📢 APPELS D'OFFRES (à répondre)",
    "prospects": "🔥 PROSPECTS (bailleurs en difficulté à contacter)",
    "juridique": "⚖️ JURIDIQUE (décisions = arguments de vente)",
    "secteur": "📋 SECTEUR / RÉGLEMENTATION (veille)",
}
_CATEGORY_ORDER = ["tenders", "prospects", "juridique", "secteur"]


def _category(row) -> str:
    """Categorie metier d'un article (par source)."""
    return _CATEGORY_BY_SOURCE.get(row.get("source"), "secteur")


def _render_article(row):
    score = _score(row)
    prio = "❗" if score >= 90 else ("🔴" if score >= 75 else "")
    fresh_icon, fresh_label = _freshness(row)
    st.markdown(f"#### {prio} {row['title']}")
    cols = st.columns([1, 2, 2])
    cols[0].metric("Score", score)
    cols[1].caption(f"Source : {row['source']}")
    cols[2].caption(f"Fraîcheur : {fresh_icon} {fresh_label}")

    summary = _summary(row)
    if summary:
        st.markdown("**Résumé :**")
        for line in summary:
            if line:
                st.write(f"• {line}")
    else:
        # A defaut de resume genere, on affiche la justification du score.
        try:
            meta = json.loads(row.get("metadata") or "{}")
        except (ValueError, TypeError):
            meta = {}
        just = meta.get("llm_justification")
        if just:
            st.caption(f"Analyse : {just}")

    st.markdown(f"[Ouvrir la source]({row['url']})")

    # Deep research : analyse approfondie a la demande.
    try:
        meta = json.loads(row.get("metadata") or "{}")
    except (ValueError, TypeError):
        meta = {}
    existing = meta.get("deep_research")

    if existing:
        _render_deep(existing)
    else:
        if st.button("🔎 Deep research", key=f"deep_{row['id']}"):
            with st.spinner("Analyse approfondie en cours..."):
                try:
                    analysis = deep_analyze(
                        row["url"], title=row["title"], client=LLMClient(config=load_settings().app.llm)
                    )
                    _save_deep(_db(), row["id"], analysis)
                    _render_deep(analysis)
                except Exception as exc:  # affichage propre en cas d'echec
                    st.error(f"Deep research indisponible pour cet article : {exc}")

    st.divider()


def _render_deep(a):
    """Affiche une fiche de deep research (structure analyste Intra-Air)."""
    niveau = (a.get("niveau") or "ACT").upper()
    icon = "🔥" if niveau == "HIGH" else "🟠"
    titre = a.get("titre_analyse") or "Analyse approfondie"
    st.markdown(f"**{icon} Deep research [{niveau}] — {titre}**")
    if a.get("organisation"):
        st.write(f"**Organisation :** {a['organisation']}")
    if a.get("contexte_resume"):
        st.write(f"**Contexte :** {a['contexte_resume']}")
    if a.get("besoin_ou_opportunite"):
        st.write(f"**Besoin / opportunité :** {a['besoin_ou_opportunite']}")
    if a.get("action"):
        st.success(f"**Action recommandée :** {a['action']}")


def main() -> None:
    st.set_page_config(page_title="Research Intelligence Agent", page_icon="📊", layout="wide")
    st.title("📊 Research Intelligence Agent — Tableau de bord")
    st.caption("Base de connaissances Intra-Air : articles collectés, filtrés et enrichis.")

    db = _db()
    db.initialize()

    tab_articles, tab_search, tab_runs = st.tabs(["Articles", "Recherche", "Runs"])

    # --- Onglet Articles (groupes par categorie) -----------------------------
    with tab_articles:
        with db.connection() as conn:
            sources = [r["source"] for r in conn.execute(
                "SELECT DISTINCT source FROM items ORDER BY source;")]
        c1, c2 = st.columns(2)
        f_source = c1.selectbox("Source", ["toutes"] + sources)
        f_status = c2.selectbox("Statut", ["kept", "tous", "dropped", "collected"], index=0)

        items = _load_items(db, source=f_source, status=f_status)
        kept = sum(1 for i in items if i.get("status") == "kept")
        m1, m2 = st.columns(2)
        m1.metric("Articles affichés", len(items))
        m2.metric("Dont retenus (kept)", kept)

        if not items:
            st.info("Aucun article pour ces filtres. Lancez un run pour peupler la base.")

        # Regroupement par CATEGORIE METIER (basee sur la source, fiable),
        # dans l'ordre de priorite commerciale.
        groups = {}
        for row in items:
            groups.setdefault(_category(row), []).append(row)

        for cat in _CATEGORY_ORDER:
            rows = groups.get(cat)
            if not rows:
                continue
            rows.sort(key=_score, reverse=True)
            st.header(f"{_CATEGORY_LABELS[cat]}  ({len(rows)})")
            for row in rows:
                _render_article(row)

    # --- Onglet Recherche ----------------------------------------------------
    with tab_search:
        query = st.text_input("Recherche plein texte (titre + contenu)")
        if query:
            results = search_items(query, limit=50, db=db)
            st.write(f"{len(results)} résultat(s)")
            for row in results:
                _render_article(row)

    # --- Onglet Runs ---------------------------------------------------------
    with tab_runs:
        with db.connection() as conn:
            runs = [dict(r) for r in conn.execute(
                "SELECT run_type, status, items_collected, items_filtered, "
                "llm_cost, started_at, finished_at FROM runs ORDER BY id DESC LIMIT 50;")]
        if runs:
            st.dataframe(runs, use_container_width=True)
        else:
            st.info("Aucun run enregistré pour l'instant.")


if __name__ == "__main__":
    main()
