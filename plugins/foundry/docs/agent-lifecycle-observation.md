# Contrat FOUNDRY-69 : observation de cycle de vie

Le contrat v1 transforme uniquement des faits fournis par une canary explicite en un rapport fermé et lisible. Il est passif : aucun appel provider ou hôte, aucune autorisation, signature, mutation de tracker, relance, retry, escalade, gate ou lancement automatique n’en fait partie.

| Champ canonique | Valeurs | Provenance | Sens |
| --- | --- | --- | --- |
| `planned` | booléen ou null | client_observed / host_reported / unavailable | plan explicite |
| `delivered_to_host` | booléen ou null | client_observed / host_reported / unavailable | livraison ou hand-off explicite |
| `terminal_observed` | booléen ou null | client_observed / host_reported / unavailable | terminal explicitement reçu |
| `timeout_observed` | booléen ou null | client_observed / host_reported / unavailable | timeout explicitement observé |
| `cancellation_observed` | booléen ou null | client_observed / host_reported / unavailable | annulation explicitement observée |

Les faits indisponibles sont `null` avec `unavailable`. Une absence de fait, même après livraison, reste `unknown`; ce contrat ne déduit jamais un terminal du silence. Au plus un des trois faits terminaux peut être vrai.

## Matrice hôte × fait canonique — v1

| Hôte | Fait | Disponibilité | Source descriptive contrôlée |
| --- | --- | --- | --- |
| Claude | `planned` | disponible | `PreToolUse` |
| Claude | `delivered_to_host` | si stream disponible | `stream task_started` |
| Claude | `terminal_observed` | disponible | `PostToolUse`, `PostToolUseFailure` ou terminal de stream |
| Claude | `timeout_observed` | indisponible | aucune |
| Claude | `cancellation_observed` | indisponible | aucune |
| Codex | `planned` | disponible | plan explicite |
| Codex | `delivered_to_host` | disponible | hand-off de sous-agent accepté |
| Codex | `terminal_observed` | disponible | reçu explicite de complétion |
| Codex | `timeout_observed` | indisponible | aucune |
| Codex | `cancellation_observed` | indisponible | aucune |

La matrice v1 est descriptive, pas un adaptateur d’exécution, et ces mécanismes ne sont pas déclarés équivalents. Les faits indisponibles restent `null/unavailable`. Les deux canaries F69 attestés fournissent une invocation unique par hôte, livraison et terminal, zéro retry/escalade automatique, ainsi que les non-mutations explicites de routage, effort, contexte, gate et résultat. Aucun fait de délai ou d’annulation n’est attesté.

La réconciliation compare deux compteurs contrôlés. Le scénario versionné reprend le compteur visuel de 241 sous-agents et un unique canari observé, mais conclut seulement `unexplained` : le compteur UI seul ne prouve ni sous-agent actif, ni stale/orphelin. Une telle conclusion exige que `stale_orphan_proof.value` soit explicitement vrai.
