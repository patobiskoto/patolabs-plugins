# PAT-19 c1 — notice opérateur (compression en un appel) — BROUILLON, rien n'a été joué

Règles : [`pat-19-protocol-c1.md`](pat-19-protocol-c1.md) (BROUILLON de la phase 1 de PAT-118 : rien n'est gelé). Le script [`pat-19-c1-operator.sh`](pat-19-c1-operator.sh) a trois modes : `pilot` (une tâche, PR 27, un candidat nommé : son appel local puis les bras F et S), `screen` (le tamis : les cinq candidats de la v1 × les 12 tâches, local, **aucune exécution cloud**) et `compare` (les 12 tâches × les bras F et S pour le candidat retenu par le tamis, **jusqu'à 36 exécutions cloud**, aucun modèle local chargé). Il n'a **jamais tourné pour de vrai** : les tests ne le jouent que jusqu'à ses refus, avec de fausses commandes `claude` et `lms`. Vérifier sur la version installée que `lms unload --all` et la commande de chargement épinglée se comportent comme dans [`pat-19-v5-operator.md`](pat-19-v5-operator.md).

Le lanceur ne charge aucun modèle. À la différence des v3 à v5, **le candidat est chargé une seule fois** et le lanceur joue ses 12 tâches : l'appel local est une requête indépendante d'un seul tour, il n'y a pas de boucle d'outils (protocole, C5 et limite L9).

## Prérequis, dans cet ordre (le script n'en vérifie que quelques-uns)

1. **Sauvegarde de l'état Foundry**, hors du dépôt, aux deux emplacements du registre ([`pat-19-v3-operator.md`](pat-19-v3-operator.md), « Avant une campagne » ; ne pas versionner cette sauvegarde, elle contient des secrets). **Non vérifiée par le script.**
2. **Machine dédiée** : le préflight existant (2 Gio / 35 %) tourne avant chaque appel local. Le mandat du mainteneur du 2026-10-08 (autorisation par bloc) couvre les chargements des cinq candidats, l'arrêt et le relancement d'OrbStack et de ChatGPT, au plus 40 exécutions cloud ; le rapport de la campagne le consigne.
3. **Serveur LM Studio démarré** sur l'adresse de boucle locale de `compression.local_call.endpoint` (`http://127.0.0.1:1234/v1/chat/completions` dans le brouillon). Le script, en modes `screen` et `pilot`, fait un `GET /v1/models` en lecture seule sur cette adresse (aucune inférence) et refuse (code 65) s'il ne répond pas. Le lanceur n'appelle jamais autre chose que cette adresse.
4. **Dossier temporaire propre** : aucun dossier contenant `plugins/foundry` au premier niveau de `$TMPDIR` (code 65 sinon).
5. **Claude Code 2.1.294** sur le `PATH` (modes `pilot` et `compare`) : le script lit `claude --version` et refuse (code 65) toute autre version, **avant** de charger un modèle ; le lanceur la contrôle aussi avant la première exécution cloud. Cette version n'a jamais été exercée par les bras du protocole.
6. **La matière** est déjà versée ([`pat-19-compression-material-v1.json`](pat-19-compression-material-v1.json)) : le lanceur ne lance aucun `pytest` pendant le tamis ni la comparaison (la consigne demande au bras de ne pas lancer la suite ; rien ne l'en empêche). Pour la régénérer (hors ligne, sans cloud ni modèle ; jamais d'écrasement, à faire dans un dossier vide) : `cd <checkout>/plugins/foundry/tooling && PYTHONPATH=. python3 -m foundry.local_first_runner compression-material --repo <repo> --work-root <work-root-jetable> --snapshot ../docs/qualification/pat-19-corpus-snapshot-v1.json --manifest ../docs/qualification/pat-19-corpus-manifest-v1.json --out-dir <dossier-vide>`.

## Pilote (à jouer par le coordinateur avant le gel)

```sh
RUNS=$HOME/pat19-c1pilot-1-runs      # hors du dépôt, propre au pilote
mkdir -p "$RUNS"
cat > "$RUNS/envelope.json" <<'JSON'
{"schema": "foundry.local-first-envelope.v1", "campaign_id": "pat-19-c1pilot-1", "expires_on": "2026-10-31",
 "allowed_modes": ["screen_compression", "compare_compression"],
 "caps": {"cloud_executions": 4, "premium_tokens": 2500000, "wall_clock_seconds": 10000}}
JSON
<checkout>/plugins/foundry/docs/qualification/pat-19-c1-operator.sh pilot qwen3.6-35b-a3b-mlx-4bit \
    <checkout> "$RUNS" <work-root> <repo>
```

Le pilote et la campagne ne partagent ni dossier `<runs>`, ni identifiant de campagne : le script refuse (code 65, avant de charger le moindre modèle) un identifiant avec `pilot` hors du mode `pilot` (ou sans lui dedans), un `state/` qui contient des fichiers `results-*` ou `ledger-*` de l'autre mode ; le lanceur refuse la même confusion (identifiant de campagne et protocole), et une enveloppe de comparaison qui nomme **plus de 4 exécutions cloud** au pilote (36 à la campagne : 36 + 4 = 40). Critères d'arrêt (a) à (g) et ce que le pilote regarde aussi : protocole, section 9. Rapport : `cd <checkout>/plugins/foundry/tooling && PYTHONPATH=. python3 -m foundry.local_first_runner report --campaign ../docs/qualification/pat-19-campaign-c1-pilot.json --results "$RUNS/state/results-pat-19-c1pilot-1.jsonl" > "$RUNS/report-pat-19-c1pilot-1.json"`. Preuve à verser sous `pat-19-runs/c1pilot-1/` (jeu de fichiers de `x4compare-1`, **sans transcription brute**) avec `pat-19-c1-pilot-evidence.md`.

## Campagne (après le gel de la phase 2, jamais avant)

```sh
RUNS=$HOME/pat19-c1run-1-runs      # hors du dépôt, jamais celui du pilote
mkdir -p "$RUNS"
cat > "$RUNS/envelope.json" <<'JSON'
{"schema": "foundry.local-first-envelope.v1", "campaign_id": "pat-19-c1run-1", "expires_on": "2026-10-31",
 "allowed_modes": ["screen_compression", "compare_compression"],
 "caps": {"cloud_executions": 36, "premium_tokens": 25000000, "wall_clock_seconds": 80000}}
JSON
SCRIPT=<checkout>/plugins/foundry/docs/qualification/pat-19-c1-operator.sh
$SCRIPT screen <checkout> "$RUNS" <work-root> <repo>              # 1. le tamis, sans cloud (60 appels locaux)
# 2. lire le rapport : compression_screening.selected / stop
cd <checkout>/plugins/foundry/tooling && PYTHONPATH=. python3 -m foundry.local_first_runner report \
    --campaign ../docs/qualification/pat-19-campaign-c1.json --results "$RUNS/state/results-pat-19-c1run-1.jsonl" | python3 -m json.tool | head -80
# 3. seulement si selected n'est pas nul (sinon : arrêt « conserver le cloud », conforme, aucune comparaison)
$SCRIPT compare <candidat-retenu> <checkout> "$RUNS" <work-root> <repo>
```

Le tamis et la comparaison partagent un même identifiant de campagne et une même enveloppe (les deux modes y sont nommés) : la comparaison relit les résumés dans les enregistrements du tamis. Plafonds recommandés dans `envelope_recommended` de `pat-19-campaign-c1.json` ; le chargeur ne vérifie que la part de 36 exécutions cloud. Une enveloppe ne change pas après le premier lancement (le registre refuse un autre digest).

## Après un arrêt, avant de relancer

Produire le rapport et lire `ledger.unknown_spent_work` (toujours imprimé) et `compression_comparison.paired_rule.campaign_level_reasons`. Les raisons de campagne (`no_result_record:`, `cloud_started_without_settled:`, `interrupted:`, `tokens_unknown:`) condamnent la campagne à `inconclusive` quoi qu'on relance ; une exécution cloud aux tokens illisibles arrête toute exécution cloud suivante (`premium_tokens_unmeasurable`, code 3) ; un appel local ou un diagnostic coupé avant tout verdict n'est rejoué **qu'une fois** ; une tâche coupée par le plafond n'est jamais rejouée ; un résumé en échec n'est jamais rejoué. Codes du lanceur : 2 refus, préflight ou erreur d'outil enregistrée ; 3 plafond atteint ; 4 le `registry.json` réel de Foundry a changé (enquêter d'abord) ; 130, 143, 129, 1 interruption ou erreur, enregistrées comme interrompues. Relancer le même script reprend.

## Ce qui n'est pas dit ici

Aucune durée, aucun coût, aucun résultat : rien n'a tourné. La PR 48 (425 688 octets) peut dépasser le contexte des candidats (protocole, section 3) : le pilote ne la joue pas.
