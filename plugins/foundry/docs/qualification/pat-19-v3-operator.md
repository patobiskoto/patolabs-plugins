# PAT-19 v3 — boucle opérateur (rechargement du modèle avant chaque tâche)

Règle : [`pat-19-protocol-v3.md`](pat-19-protocol-v3.md) section 4. Le lanceur ne charge aucun modèle ; il joue au plus une tâche par lancement et écrit `pat19-v3: work_remains=yes|no`. L'opérateur décharge et recharge le modèle (commande épinglée `load_command` de la configuration) avant **chaque** lancement. Avant de lancer : enveloppe, instantané, manifeste, dépôt complet et répertoires comme pour la v2 (section « Protocole v2 » de [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md)). À lancer depuis `plugins/foundry/tooling` (`PYTHONPATH=.`), sur la machine dédiée, hors de tout bac à sable.

Tamis, un candidat à la fois dans l'ordre gelé (`qwen3.6-35b-a3b-mlx-4bit` puis `qwen3-coder-30b-a3b-mlx-4bit`) ; la commande `lms unload --all` est celle du CLI LM Studio (à vérifier sur la version installée avant l'essai) :

```sh
set -eu
CFG=docs/qualification/pat-19-campaign-v3.json   # chemin relatif à plugins/foundry
for CAND in qwen3.6-35b-a3b-mlx-4bit qwen3-coder-30b-a3b-mlx-4bit; do
  while :; do
    lms unload --all
    python3 -c 'import json,shlex,sys; print(shlex.join(json.load(open(sys.argv[1]))["candidates"][sys.argv[2]]["load_command"]))' "$CFG" "$CAND" | sh
    OUT=$(python3 -m foundry.local_first_runner screen-exploration --campaign "$CFG" --candidate "$CAND" \
          --envelope "$ENV" --state-dir "$STATE" --work-root "$WORK" --repo "$REPO" \
          --snapshot "$SNAP" --manifest "$MANIFEST" --sandbox)   # set -e : un code non nul (2, 3) arrête la boucle
    echo "$OUT"
    case "$OUT" in *"pat19-v3: work_remains=yes"*) ;; *) break ;; esac
  done
done
```

Comparaison : même boucle autour de `compare-exploration` (un seul `--candidate`, le candidat retenu par le tamis ; `--paths A,L,E`) ; une tâche de comparaison (tous ses bras) par lancement.

Garanties : un lancement qui ne joue rien (tout est décidé) répond `work_remains=no`, donc la boucle se termine ; un arrêt (préflight refusé, code 2 ; plafond atteint, code 3) interrompt la boucle et se reprend en relançant la même boucle (reprise inchangée : une tâche décidée n'est pas rejouée). Le registre contient un préflight par lancement ; c'est, avec cette boucle, la trace du rechargement par tâche.
