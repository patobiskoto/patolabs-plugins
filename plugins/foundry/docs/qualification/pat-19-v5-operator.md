# PAT-19 v5 — boucle opérateur (rechargement du modèle avant chaque tâche, bras A et L) — BROUILLON

Règles : [`pat-19-protocol-v5.md`](pat-19-protocol-v5.md) (brouillon, non gelé ; section 4 de la [v3](pat-19-protocol-v3.md) pour le rechargement). Le script [`pat-19-v5-operator.sh`](pat-19-v5-operator.sh) est la copie du [script v4](pat-19-v4-operator.sh) avec deux modes : `compare` (la campagne : `pat-19-campaign-v5.json`, 12 tâches) et `pilot` (le pilote : `pat-19-campaign-v5-pilot.json`, PR 27 seule). Même boucle, `--paths A,L`, ni `screen` ni `--screening-campaign` (le candidat est fixé par la configuration ; `screen-exploration` y est refusé). Le lanceur écrit toujours `pat19-v3: work_remains=yes|no` (ligne inchangée).

```sh
pat-19-v5-operator.sh pilot   qwen3.6-35b-a3b-mlx-4bit <checkout> <runs> <work-root> <repo>
pat-19-v5-operator.sh compare qwen3.6-35b-a3b-mlx-4bit <checkout> <runs> <work-root> <repo>
```

- `<checkout>` : checkout d'outillage ; `<runs>` : `envelope.json` (lu), `state/` et le journal opérateur horodaté ; `<work-root>` : jetable, hors de tout checkout ; `<repo>` : clone complet d'où sont construites les tâches. Comme en v4 ([`pat-19-v4-operator.md`](pat-19-v4-operator.md), [`pat-19-v3-operator.md`](pat-19-v3-operator.md)).
- **Le pilote et la campagne ne partagent ni dossier `<runs>`, ni identifiant de campagne.** Le script refuse (code 65, **avant de charger le moindre modèle**) : un identifiant de campagne de l'enveloppe sans `pilot` en mode `pilot`, ou avec `pilot` en mode `compare` ; un `state/` qui contient des fichiers `results-*` ou `ledger-*` de l'autre mode ; un `python3` du shell qui n'importe pas pytest (l'interpréteur résolu est affiché) ; une version de Claude Code sur le `PATH` autre que celle épinglée par la configuration (2.1.285). Le lanceur refuse lui aussi l'identifiant de campagne incohérent avec le protocole et vérifie la même version avant toute réservation.
- Par itération : `lms unload --all`, chargement épinglé, relevé de `lms ps --json`, un lancement (une tâche, les deux bras), journal du code de sortie et de la ligne `work_remains`. Tout code autre que 0 arrête la boucle (2 refus/préflight, dont une version de Claude Code différente ; 3 plafond ; 4 registre Foundry réel modifié par un bras cloud : enquêter avant tout relancement) ; relancer le même script reprend.
- **Le script ne vérifie pas la sauvegarde** (ni son existence ni son contenu) : c'est un préalable de l'opérateur (section ci-dessous). Le mandat du mainteneur du 2026-10-09 couvre les chargements de modèle et les arrêts de services de la journée ; le rapport de la campagne le consigne (aucune autorisation permanente n'est supposée au-delà).
- Le script n'a pas été joué pour de vrai (aucun appel modèle ni cloud) ; `lms unload --all` est à vérifier sur la version installée avant l'essai. Les tests ne le jouent qu'avec de fausses commandes `claude` et `lms` jusqu'à ses refus.

**Lancer le script depuis un shell propre.** Le pilote 1 (`pat-19-x5pilot-1`) a été **nul** parce que le `PATH` du shell mettait `/opt/homebrew/bin` devant miniconda : le `python3` (Python 3.14 de Homebrew) n'avait pas pytest, le lanceur a tourné sous lui, le juge n'a rien pu exécuter et les bras ont hérité du même `PATH` (voir la section 7.3 bis du protocole). Le lanceur et le script refusent maintenant ce cas avant tout modèle, mais le bon réflexe reste : `command -v python3 && python3 -c "import pytest"` avant de lancer. Auto-contrôle hors ligne du juge (aucun modèle, aucun cloud) :

```sh
cd <checkout>/plugins/foundry/tooling && PYTHONPATH=. python3 -m foundry.local_first_runner golden-check \
    --campaign ../docs/qualification/pat-19-campaign-v5.json --repo <repo> --work-root <work-root-jetable> \
    --snapshot ../docs/qualification/pat-19-corpus-snapshot-v1.json --manifest ../docs/qualification/pat-19-corpus-manifest-v1.json \
    --out <fichier-resultat.json>      # code 0 si les 12 tâches sont conformes ; jamais d'écrasement
```

## Pilote : préparation et commande exacte

Le pilote 1 est nul (voir ci-dessus) ; les pilotes 1 (nul) et 2 (critère (c), voir la section 7.3 ter du protocole) sont joués ; le pilote 3 (`pat-19-x5pilot-3`, son propre dossier d'exécution) est joué **une fois, avant le gel**, hors décision (voir la section 7 du protocole : critère d'arrêt, ce qu'il regarde, où va son résultat). Écrire l'enveloppe du pilote (le lanceur n'en écrit jamais) dans un dossier d'exécution **propre au pilote**, hors du dépôt :

```sh
RUNS=$HOME/pat19-x5pilot-3-runs      # hors du dépôt, jamais celui de la campagne
mkdir -p "$RUNS"
cat > "$RUNS/envelope.json" <<'JSON'
{"schema": "foundry.local-first-envelope.v1", "campaign_id": "pat-19-x5pilot-3", "expires_on": "2026-10-31",
 "allowed_modes": ["compare_exploration"],
 "caps": {"cloud_executions": 16, "premium_tokens": 8000000, "wall_clock_seconds": 30000}}
JSON
<checkout>/plugins/foundry/docs/qualification/pat-19-v5-operator.sh pilot qwen3.6-35b-a3b-mlx-4bit \
    <checkout> "$RUNS" <work-root-pilote> <repo>
```

Un seul lancement joue la tâche (PR 27, bras A et L complets) et le script s'arrête à `work_remains=no`. Le résultat se verse sous `pat-19-runs/x5pilot-3/` : `envelope.json`, `ledger-pat-19-x5pilot-3.jsonl`, `operator-pilot-*.log`, `results-pat-19-x5pilot-3.jsonl`, `streams-manifest.json` et le rapport (`cd <checkout>/plugins/foundry/tooling && PYTHONPATH=. python3 -m foundry.local_first_runner report --campaign ../docs/qualification/pat-19-campaign-v5-pilot.json --results "$RUNS/state/results-pat-19-x5pilot-3.jsonl" > "$RUNS/report-pat-19-x5pilot-3.json"`), **sans transcript brut** (les flux de `"$RUNS/state/streams/"` restent hors dépôt).

## Campagne : enveloppe recommandée

Après le gel seulement : identifiant `pat-19-x5compare-1`, `allowed_modes` `["compare_exploration"]`, plafonds **150 exécutions cloud, 75 000 000 jetons premium, 100 000 s** (`envelope_recommended` de `pat-19-campaign-v5.json` ; le chargeur ne le vérifie pas, c'est l'enveloppe de l'opérateur qui s'applique).

## Avant le pilote et la campagne : sauvegarder l'état Foundry (préalable du protocole)

La sauvegarde couvre les **deux** emplacements du registre, à faire **hors du dépôt** (elle contient des secrets : ne la versionner ni la copier dans un dépôt, une note ou la mémoire) : voir [`pat-19-v3-operator.md`](pat-19-v3-operator.md), section « Avant une campagne » (commandes écrites avec `if … ; then cp … ; fi`, sûres sous `set -e`), et ne lancer aucune commande Foundry qui écrit le registre pendant le pilote ou la campagne. Machine dédiée : le préflight du lanceur refuse un processus étranger de plus de 2 Gio ou moins de 35 % de mémoire libre.
