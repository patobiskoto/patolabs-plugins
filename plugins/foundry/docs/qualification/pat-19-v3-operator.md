# PAT-19 v3 — boucle opérateur (rechargement du modèle avant chaque tâche)

Règle : [`pat-19-protocol-v3.md`](pat-19-protocol-v3.md) section 4. Le lanceur ne charge aucun modèle ; il joue au plus une tâche par lancement et écrit `pat19-v3: work_remains=yes|no`. Le script [`pat-19-v3-operator.sh`](pat-19-v3-operator.sh) (bash, `set -euo pipefail`) décharge et recharge le modèle, avec la commande épinglée `load_command` de `pat-19-campaign-v3.json` (+ `-y`), avant **chaque** lancement.

```sh
pat-19-v3-operator.sh screen  <candidat>                       <checkout> <runs> <work-root> <repo>
pat-19-v3-operator.sh compare <candidat> <id-campagne-tamis>   <checkout> <runs> <work-root> <repo>
```

- `<checkout>` : checkout d'outillage ; la configuration est lue dans `<checkout>/plugins/foundry/docs/qualification/pat-19-campaign-v3.json`, le lanceur est exécuté depuis `<checkout>/plugins/foundry/tooling`.
- `<runs>` : contient `envelope.json` (lu) et `state/` (résultats, registre) ; le journal opérateur horodaté `operator-<mode>-<candidat>-<horodatage>.log` y est écrit, à verser avec les résultats bruts.
- Tamis : un candidat à la fois, dans l'ordre gelé (`qwen3.6-35b-a3b-mlx-4bit` puis `qwen3-coder-30b-a3b-mlx-4bit`). Comparaison : `--paths A,L,E` et `--screening-campaign <id>`.
- Par itération : `lms unload --all`, chargement épinglé (échec explicite si la commande est vide), relevé de `lms ps --json` (identifiant, clé, quantification, contexte), un lancement (entrée standard fermée), journal du code de sortie et de la ligne `work_remains`. Boucle tant que le lanceur sort en 0 avec `work_remains=yes` ; tout autre code (2 refus/préflight, 3 plafond, 4 registre Foundry réel modifié par un bras cloud : enquêter avant tout relancement) arrête la boucle et le dit ; relancer le même script reprend (une tâche décidée n'est pas rejouée). `lms unload --all` final.
- Le bac à sable est actif par défaut (pas de `--dry-run`). `lms unload --all` est à vérifier sur la version installée avant l'essai ; le script n'a pas été joué pour de vrai (aucun appel modèle).
- Le registre contient un préflight par lancement ; avec le journal opérateur, c'est la trace du rechargement par tâche (le lanceur ne l'atteste pas).

## Avant une campagne : sauvegarder la configuration Foundry (PAT-120)

Le lanceur impose maintenant `FOUNDRY_DATA` aux bras et arrête la campagne si le `registry.json` réel change (voir « État Foundry d'un bras cloud » dans [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md)), mais un bras cloud garde le vrai HOME : sauvegarder à la main, **hors du dépôt**, avant tout lancement (le script ne copie rien, la sauvegarde contient des secrets : ne la versionner ni la copier dans un dépôt, une note ou la mémoire) :

```sh
cp -Rp "${FOUNDRY_DATA:-$HOME/.config/foundry}" "$HOME/foundry-backup-$(date -u +%Y%m%dT%H%M%SZ)"
```

Sortie 4 du lanceur : ne rien relancer ; comparer l'empreinte du `registry.json` avec la sauvegarde, restaurer à la main si besoin, puis seulement reprendre.
