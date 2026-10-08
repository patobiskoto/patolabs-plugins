# PAT-19 v5 — pièces des quatre pilotes de l'instrument et contrôles hors ligne (PAT-126)

Pièces versées sans transcript brut, jouées le 2026-10-08 (protocole [`pat-19-protocol-v5.md`](pat-19-protocol-v5.md), section 7). Aucune n'entre dans le bilan de la campagne ; une tâche (PR 27) déjà vue ne prouve rien et ne règle rien.

- `pat-19-runs/x5pilot-1/` : pilote 1, **nul** (`PATH` d'opérateur : `python3` de Homebrew sans pytest, juge à 0/0/0, aucun relecteur, 758 999 jetons).
  Rapport régénéré avec l'outillage de la revue du round 1 à partir de la configuration du commit `834fca6` (empreinte égale à celle des enregistrements).
- `pat-19-runs/x5pilot-2/` : pilote 2, **critère (c)** (deux PASS écartés par l'audit : un appel refusé par l'hôte compté comme exécuté, un `cd` non cru après des tests en échec ; 1 825 779 jetons).
  Rapport régénéré avec l'outillage de la revue du round 1 à partir de la configuration du commit `9aa04fc` (même empreinte que le pilote 1).
- `pat-19-runs/x5pilot-3/` : pilote 3, **critère (c)** (un PASS écarté par un chemin tronqué affiché dans une sortie ; 1 657 127 jetons, première acceptation).
  Rapport régénéré avec l'outillage de la revue du round 1 à partir de la configuration du commit `22610ce` ; le rapport produit alors avec `22610ce` n'est pas conservé.
- `pat-19-runs/x5pilot-4/` : pilote 4, **passe les critères (a) à (f)** (1 595 725 jetons ; A et L acceptés ; journal : 2 entrées `tool_result:~/.config/foundry/config.env`, échos de tests du bundle sous le bac à sable).
  Rapport **tel que produit par le coordinateur avec l'outillage `361f719`, conservé tel quel** ; `paired_rule` dit `inconclusive` (|D| < 9) : attendu sur une tâche. Sa première tentative, refusée avant tout modèle par le script (copies de dépôt dans le dossier temporaire), n'a pas de pièces.
- `pat-19-runs/x5pilot-N/replay-audit-pat-19-x5pilot-N.json` : rejeu hors ligne de l'audit (révision 1, 2 et politique finale) sur les flux de chaque pilote (1 à 4), avec `pat-19-campaign-v5-pilot.json`.
  Résultat : aucun constat décisif sur aucun relecteur ; `fidelity: mismatch` (`not_comparable`) des enregistrements dont les constats enregistrés ne contiennent que le décisif de l'époque.
- **Aucun rapport de pilote n'est régénéré avec l'outillage des rounds 2, 3 et 4 de la revue** : leur `paired_rule` n'a pas les champs ajoutés alors (`verdict_before_campaign_level`, `campaign_level_reasons`, `compatibility`), leur `economy_detail` porte l'ancien calcul et leurs enregistrements l'ancienne forme de `audit.temp_leftovers` (`count` et `names`, sans `watch`) ; leur verdict (`inconclusive`, |D| < 9) n'en dépend pas.
- `pat-19-golden-check-v5.json` (**absent de ce commit** : il est produit depuis le commit gelé, dans le commit qui le suit ; des fichiers produits depuis les gels précédents avaient été versés — depuis `56634fe` au commit `4f95b86`, depuis `e6f90c6` au commit `52ef9d1`, puis depuis `bbbb8ef` au commit `5b74996` : le dernier est de nouveau retiré de l'arbre, parce que ce gel a été rouvert par le round 4 de la revue, et **doit être régénéré par le coordinateur depuis le nouveau commit de gel**) : `golden-check` sur les 12 tâches avec `pat-19-campaign-v5.json` gelée (aucun modèle, aucun cloud), provenance incluse (empreintes de la configuration, du manifeste et de l'instantané, commit d'outillage, arbre modifié ou non, date).
  Chaque tâche intacte doit être REFUSÉE avec au moins un test caché en échec (jamais 0/0/0), chaque tâche avec le changement fusionné ACCEPTÉE. Aucun test ne dépend de ce fichier.

Les identifiants de session et les chemins de la racine de travail y restent, comme dans `x4compare-1` ; aucun chemin du home ni nom d'utilisateur (vérifié par recherche).
