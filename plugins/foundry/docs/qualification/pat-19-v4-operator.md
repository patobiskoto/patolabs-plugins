# PAT-19 v4 — boucle opérateur (rechargement du modèle avant chaque tâche, bras A et L)

Règles : [`pat-19-protocol-v4.md`](pat-19-protocol-v4.md) (section 4 de la [v3](pat-19-protocol-v3.md) pour le rechargement). Le script [`pat-19-v3-operator.sh`](pat-19-v3-operator.sh) lit en dur la configuration v3 et les bras `A,L,E` : il ne peut pas piloter la v4. [`pat-19-v4-operator.sh`](pat-19-v4-operator.sh) en est la copie réduite à la comparaison : même boucle, configuration `pat-19-campaign-v4.json`, `--paths A,L`, ni `screen` ni `--screening-campaign` (le candidat est fixé par la configuration ; `screen-exploration` y est refusé). Le lanceur écrit toujours `pat19-v3: work_remains=yes|no` (ligne inchangée).

```sh
pat-19-v4-operator.sh compare qwen3.6-35b-a3b-mlx-4bit <checkout> <runs> <work-root> <repo>
```

- `<checkout>`, `<runs>` (`envelope.json`, `state/`, journal opérateur horodaté), `<work-root>` (jetable, hors de tout checkout), `<repo>` (clone complet d'où sont construites les tâches) : comme en v3 ([`pat-19-v3-operator.md`](pat-19-v3-operator.md)). L'enveloppe recommandée (80 exécutions cloud) est dans `pat-19-campaign-v4.json`, `envelope_recommended` ; elle reste celle de l'opérateur.
- Par itération : `lms unload --all`, chargement épinglé, relevé de `lms ps --json`, un lancement (une tâche, les deux bras), journal du code de sortie et de la ligne `work_remains`. Tout code autre que 0 arrête la boucle (2 refus/préflight, 3 plafond, 4 registre Foundry réel modifié par un bras cloud : enquêter avant tout relancement) ; relancer le même script reprend.
- Le script n'a pas été joué pour de vrai (aucun appel modèle ni cloud) ; `lms unload --all` est à vérifier sur la version installée avant l'essai.

## Avant la campagne : sauvegarder l'état Foundry (préalable du protocole)

La sauvegarde couvre les **deux** emplacements du registre, à faire **hors du dépôt** (elle contient des secrets : ne la versionner ni la copier dans un dépôt, une note ou la mémoire) : voir [`pat-19-v3-operator.md`](pat-19-v3-operator.md), section « Avant une campagne », et ne lancer aucune commande Foundry qui écrit le registre pendant la campagne.
