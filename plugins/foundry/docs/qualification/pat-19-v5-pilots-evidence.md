# PAT-19 v5 — pièces des trois pilotes de l'instrument et contrôles hors ligne (PAT-126)

Pièces versées sans transcript brut, jouées le 2026-10-08 (protocole [`pat-19-protocol-v5.md`](pat-19-protocol-v5.md), section 7). Aucune n'entre dans le bilan de la campagne ; une tâche (PR 27) déjà vue ne prouve rien et ne règle rien.

- `pat-19-runs/x5pilot-1/` : pilote 1, **nul** (`PATH` d'opérateur : `python3` de Homebrew sans pytest, juge à 0/0/0, aucun relecteur, 758 999 jetons).
  Rapport régénéré avec l'outillage du gel à partir de la configuration du commit `834fca6` (empreinte égale à celle des enregistrements).
- `pat-19-runs/x5pilot-2/` : pilote 2, **critère (c)** (deux PASS écartés par l'audit : un appel refusé par l'hôte compté comme exécuté, un `cd` non cru après des tests en échec ; 1 825 779 jetons).
  Rapport régénéré avec l'outillage du gel à partir de la configuration du commit `9aa04fc` (même empreinte que le pilote 1).
- `pat-19-runs/x5pilot-3/` : pilote 3, **critère (c)** (un PASS écarté par un chemin tronqué affiché dans une sortie ; 1 657 127 jetons, première acceptation).
  Rapport régénéré avec l'outillage du gel à partir de la configuration du commit `22610ce` ; le rapport produit alors avec `22610ce` n'est pas conservé.
- `pat-19-runs/x5pilot-N/replay-audit-pat-19-x5pilot-N.json` : rejeu hors ligne de l'audit (révision 1, 2 et politique finale) sur les flux de chaque pilote, avec `pat-19-campaign-v5-pilot.json` à l'état du gel.
  Résultat : aucun constat décisif sur aucun relecteur ; `fidelity: mismatch` (`not_comparable`) des enregistrements dont les constats enregistrés ne contiennent que le décisif de l'époque.
- `pat-19-golden-check-v5.json` : `golden-check` sur les 12 tâches avec `pat-19-campaign-v5.json` gelée (aucun modèle, aucun cloud).
  Chaque tâche intacte est REFUSÉE avec au moins un test caché en échec (jamais 0/0/0), chaque tâche avec le changement fusionné est ACCEPTÉE : 12 sur 12.

Les identifiants de session et les chemins de la racine de travail y restent, comme dans `x4compare-1` ; aucun chemin du home ni nom d'utilisateur (vérifié par recherche).
