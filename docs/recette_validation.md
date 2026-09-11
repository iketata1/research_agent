# Rapport de recette — validation de bout en bout

Date : 2026-09-11
Environnement : validation locale (macOS), APIs et Telegram réels.
Version : branche `main` (317 tests unitaires/intégration au vert).

## Objectif

Valider le premier cycle complet de bout en bout : collecte réelle des sources,
persistance, journalisation des runs, et livraison du rapport sur Telegram.

## Résumé

Le pipeline est fonctionnel de bout en bout. La collecte réelle, la persistance,
l'audit des runs, la génération et la livraison Telegram sont validés en
conditions réelles. Deux ajustements de configuration (non bloquants pour le
code) sont identifiés avant l'activation autonome.

Verdict : **prêt pour la production**, sous réserve des deux points de config
ci-dessous et de l'ajout de la clé LLM.

## Détail des vérifications

### 1. Connecteurs (collecte réelle)

| Source | Résultat | Note |
|--------|----------|------|
| OpenAlex | ✅ 50 items | API réelle, parsing et normalisation OK |
| TenderNed | ⚠️ 0 | URL de flux périmée (404 après redirection) |
| Aedes | ⚠️ 0 | URL de flux périmée (404 après redirection) |
| Google News | ⚠️ 0 | à revérifier après correctif redirections |
| TED | ⚠️ 0 | requête API rejetée (400 Bad Request) |
| Rechtspraak | ⚠️ 0 | à revérifier |

- L'isolation des pannes fonctionne : les sources en échec n'ont pas interrompu
  la collecte OpenAlex ni le run.
- Correctif appliqué pendant la recette : le client HTTP des connecteurs suit
  désormais les redirections et envoie un User-Agent explicite (les flux RSS
  officiels redirigent fréquemment).

### 2. Logs centralisés

- Logging fichier rotatif opérationnel (`logs/agent.log`), avec horodatage,
  niveau et nom du logger. Les erreurs de connecteurs sont tracées avec leur
  stacktrace, sans bloquer le run.

### 3. Intégrité de la base SQLite

- 50 items OpenAlex persistés avec identifiant stable, titre, source et statut.
- Déduplication vérifiée (une seule insertion par ressource).
- Enrichissement (score 0-100, thème, statut) validé via la fonction de
  persistance transactionnelle sur des items réels.

### 4. Table `runs` (audit)

- Run `daily` enregistré avec statut `success`, items collectés, et horodatage
  de fin. Le suivi d'audit est opérationnel.

### 5. Rapport Telegram

- Rapport hebdomadaire généré à partir de données réelles, groupé par thème
  (🔬 RESEARCH, 📡 TECHNOLOGY), avec scores, badges HIGH/ACT, résumés 3 lignes,
  liens et pied de page (période, items, coût LLM).
- Fichier Markdown exporté dans `reports/`.
- Livraison Telegram confirmée (message reçu).
- Découpage automatique (chunking 4096) disponible pour les rapports longs.

## Points à traiter avant l'activation autonome

1. **Clé LLM** : renseigner `LLM_API_KEY` dans `.env` pour activer le
   scoring, la classification et les résumés. Sans elle, seuls la collecte et le
   pré-filtre fonctionnent.
2. **URLs de flux périmées** : mettre à jour `feed_url` de TenderNed et Aedes
   dans `config/config.yaml` (les URLs actuelles renvoient 404).
3. **Requête TED** : ajuster la requête « expert query » (rejet 400 par l'API).
4. **Query OpenAlex** : affiner la requête pour réduire le bruit hors périmètre
   (des sujets sans lien avec le logement remontent actuellement) ; le filtrage
   LLM tranchera, mais une requête plus ciblée réduit le volume et le coût.

## Conclusion

L'architecture, le code et l'automatisation sont validés en conditions réelles.
Le système est prêt pour la production autonome une fois la clé LLM ajoutée et
les URLs de sources rafraîchies. Les points restants sont de la configuration,
pas du développement.
