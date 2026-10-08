# PAT-19 v5 — boucle opérateur (rechargement du modèle avant chaque tâche, bras A et L)

Règles : [`pat-19-protocol-v5.md`](pat-19-protocol-v5.md) (brouillon, gel après le pilote 4 ; section 4 de la [v3](pat-19-protocol-v3.md) pour le rechargement). Le script [`pat-19-v5-operator.sh`](pat-19-v5-operator.sh) est la copie du [script v4](pat-19-v4-operator.sh) avec deux modes : `compare` (la campagne : `pat-19-campaign-v5.json`, 12 tâches, identifiant `pat-19-x5compare-1`, **à lancer après le gel seulement**) et `pilot` (configuration des pilotes, PR 27 seule : trois pilotes sont joués, le pilote 4 reste à jouer, voir le protocole, section 7). Même boucle, `--paths A,L`, ni `screen` ni `--screening-campaign` (le candidat est fixé par la configuration ; `screen-exploration` y est refusé). Le lanceur écrit toujours `pat19-v3: work_remains=yes|no` (ligne inchangée).

## Prérequis, dans cet ordre

1. **Shell propre.** Lancer le script depuis un shell dont le `python3` est celui qui a pytest, sans PATH bricolé : `command -v python3 && python3 -c "import pytest"`. **Le pilote 1 (`pat-19-x5pilot-1`) a été nul pour cette raison** : `/opt/homebrew/bin` devant miniconda, un Python 3.14 sans pytest, un juge qui ne pouvait rien exécuter (protocole, 7.3 bis). Le script refuse (code 65, avant tout modèle) un `python3` sans pytest et affiche l'interpréteur résolu ; le lanceur refuse aussi (préflight des interpréteurs, code 2, avant tout modèle ou réservation cloud : le lanceur, le `python3` et le `python` du `PATH` donné aux bras doivent importer pytest ; chemins et version de pytest dans l'entrée `preflight` du registre).
2. **Auto-contrôle du juge, hors ligne** (aucun modèle, aucun cloud, pas de `claude`) :

```sh
cd <checkout>/plugins/foundry/tooling && PYTHONPATH=. python3 -m foundry.local_first_runner golden-check \
    --campaign ../docs/qualification/pat-19-campaign-v5.json --repo <repo> --work-root <work-root-jetable> \
    --snapshot ../docs/qualification/pat-19-corpus-snapshot-v1.json --manifest ../docs/qualification/pat-19-corpus-manifest-v1.json \
    --out <fichier-resultat.json>      # code 0 si les 12 tâches sont conformes ; jamais d'écrasement
```

3. **Dossier temporaire propre** : aucun dossier contenant `plugins/foundry` au premier niveau de `$TMPDIR` (reste d'un relecteur des pilotes, par exemple `rv`) ; le script refuse (code 65) sinon. Le lanceur déplace et consigne les copies du bundle qu'une exécution y laisse (protocole, 6.2).
4. **Sauvegarde de l'état Foundry**, hors du dépôt (section ci-dessous), **machine dédiée** (le préflight refuse un processus étranger de plus de 2 Gio ou moins de 35 % de mémoire libre), **enveloppe** (ci-dessous).

## Pilote 4 : enveloppe et commande exactes

Même chose que la campagne avec l'identifiant `pat-19-x5pilot-4` et le mode `pilot` (un dossier d'exécution propre, jamais celui d'un pilote précédent ni de la campagne) :

```sh
RUNS=$HOME/pat19-x5pilot-4-runs
mkdir -p "$RUNS"
cat > "$RUNS/envelope.json" <<'JSON'
{"schema": "foundry.local-first-envelope.v1", "campaign_id": "pat-19-x5pilot-4", "expires_on": "2026-10-31",
 "allowed_modes": ["compare_exploration"],
 "caps": {"cloud_executions": 16, "premium_tokens": 8000000, "wall_clock_seconds": 30000}}
JSON
<checkout>/plugins/foundry/docs/qualification/pat-19-v5-operator.sh pilot qwen3.6-35b-a3b-mlx-4bit \
    <checkout> "$RUNS" <work-root-pilote> <repo>
```

Il doit passer les critères d'arrêt (a) à (f) du protocole (7.2) pour que le gel ait lieu ; son rapport imprimera |D| < 9 donc `inconclusive` : attendu.

## Campagne : enveloppe et commande exactes (après le gel)

Écrire l'enveloppe dans un dossier d'exécution **propre à la campagne**, hors du dépôt, jamais celui d'un pilote (le lanceur n'en écrit jamais ; plafonds **150 exécutions cloud, 75 000 000 jetons premium, 100 000 s** = `envelope_recommended` de `pat-19-campaign-v5.json`, que le chargeur ne vérifie pas) :

```sh
RUNS=$HOME/pat19-x5compare-1-runs      # hors du dépôt
mkdir -p "$RUNS"
cat > "$RUNS/envelope.json" <<'JSON'
{"schema": "foundry.local-first-envelope.v1", "campaign_id": "pat-19-x5compare-1", "expires_on": "2026-10-31",
 "allowed_modes": ["compare_exploration"],
 "caps": {"cloud_executions": 150, "premium_tokens": 75000000, "wall_clock_seconds": 100000}}
JSON
<checkout>/plugins/foundry/docs/qualification/pat-19-v5-operator.sh compare qwen3.6-35b-a3b-mlx-4bit \
    <checkout> "$RUNS" <work-root> <repo>
```

- `<checkout>` : checkout d'outillage ; `<runs>` : `envelope.json` (lu), `state/` et le journal opérateur horodaté ; `<work-root>` : jetable, hors de tout checkout ; `<repo>` : clone complet d'où sont construites les tâches. Comme en v4 ([`pat-19-v4-operator.md`](pat-19-v4-operator.md), [`pat-19-v3-operator.md`](pat-19-v3-operator.md)).
- **La campagne et les pilotes ne partagent ni dossier `<runs>`, ni identifiant de campagne.** Le script refuse (code 65, **avant de charger le moindre modèle**) : un identifiant de campagne avec `pilot` en mode `compare` (ou sans en mode `pilot`) ; un `state/` qui contient des fichiers `results-*` ou `ledger-*` de l'autre mode ; un `python3` du shell sans pytest ; une version de Claude Code sur le `PATH` autre que celle épinglée (2.1.285). Le lanceur refuse lui aussi un identifiant incohérent avec le protocole et vérifie la même version avant toute réservation.
- Par itération : `lms unload --all`, chargement épinglé, relevé de `lms ps --json`, un lancement (une tâche, les deux bras), journal du code de sortie et de la ligne `work_remains`. Tout code autre que 0 arrête la boucle (2 refus/préflight, dont une version de Claude Code différente ou un interpréteur sans pytest ; 3 plafond ; 4 registre Foundry réel modifié par un bras cloud : enquêter avant tout relancement) ; relancer le même script reprend. Un `tool_error` du juge (rapport pytest absent) arrête la campagne : enquêter avant de reprendre.
- **Le script ne vérifie pas la sauvegarde** (ni son existence ni son contenu) ni ne rejoue `golden-check` : ce sont des préalables de l'opérateur. Le mandat du mainteneur du 2026-10-08 couvre les chargements de modèle et les arrêts de services de la journée ; le rapport de la campagne le consigne (aucune autorisation permanente n'est supposée au-delà).
- Le script n'a pas été joué pour de vrai hors des trois pilotes ; `lms unload --all` est à vérifier sur la version installée. Les tests ne le jouent qu'avec de fausses commandes `claude` et `lms`, jusqu'à ses refus.
- Rapport : `cd <checkout>/plugins/foundry/tooling && PYTHONPATH=. python3 -m foundry.local_first_runner report --campaign ../docs/qualification/pat-19-campaign-v5.json --results "$RUNS/state/results-pat-19-x5compare-1.jsonl" > "$RUNS/report-pat-19-x5compare-1.json"`. À verser sous `pat-19-runs/x5compare-1/` comme `x4compare-1` (jeu de fichiers identique, **sans transcript brut** : les flux de `"$RUNS/state/streams/"` restent hors dépôt). Le rapport lit chaque constat décisif **sur le transcript** avant de le qualifier (protocole, 7.6) et donne `audit_journal` (journal par bras et par rôle, appels refusés par l'hôte).

## Les pilotes (joués, pour mémoire)

Les pilotes 1 (nul), 2 et 3 ont été joués le 2026-10-08 avec `pat-19-v5-operator.sh pilot …`, sous `pat-19-x5pilot-1` à `-3`, chacun dans son dossier d'exécution (protocole, section 7 ; pièces sous `pat-19-runs/x5pilot-N/`). Le quatrième est requis avant le gel.

## Avant le pilote et la campagne : sauvegarder l'état Foundry (préalable du protocole)

La sauvegarde couvre les **deux** emplacements du registre, à faire **hors du dépôt** (elle contient des secrets : ne la versionner ni la copier dans un dépôt, une note ou la mémoire) : voir [`pat-19-v3-operator.md`](pat-19-v3-operator.md), section « Avant une campagne » (commandes écrites avec `if … ; then cp … ; fi`, sûres sous `set -e`), et ne lancer aucune commande Foundry qui écrit le registre pendant la campagne.
