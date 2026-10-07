# PAT-19 — Protocole de qualification local-first, version 4 : instrument réparé, comparaison A / L sur les six tâches du tamis v3

PAT-121. Cadre : PAT-ADR-0015 (exploration évaluée seulement par son effet aval, règle écrite d'avance, coût net par tâche acceptée, une donnée absente n'est jamais zéro, preuve insuffisante = conserver le cloud, aucune promotion, un protocole gelé n'est jamais édité : une nouvelle version s'écrit), FOUNDRY-ADR-0019 (comparaison bornée, règle fixée d'avance, pas de second cadre), FOUNDRY-ADR-0010 (enveloppe vérifiée avant tout appel cloud), FOUNDRY-ADR-0015 (coût lu dans les journaux de session), FOUNDRY-ADR-0007 (aucun rôle local dans le produit). Tout changement d'une coordonnée gelée (ci-dessous ou héritée) ouvre une version 5.

**Gelé avant tout essai v4.** Aucune tentative v4 n'a eu lieu. Ce protocole **ne modifie pas** les protocoles v1, v2 et v3, leurs configurations (`pat-19-campaign-v1.json`, `-v2.json`, `-v3.json`) ni leurs résultats ([`pat-19-exploration-results-v2.md`](pat-19-exploration-results-v2.md), [`pat-19-exploration-results-v3.md`](pat-19-exploration-results-v3.md), `pat-19-runs/`).

**Qui a validé quoi, et quand.** Validé par le mainteneur le **2026-10-07** : deux bras seulement (A, Sonnet seul ; L, Sonnet avec le rapport de l'explorateur local), pas de bras Haiku E ; candidat fixé `qwen3.6-35b-a3b-mlx-4bit`, sans nouveau tamis, mêmes bornes (60 étapes, 15 minutes) et même rechargement avant chaque tâche que la v3 ; comparaison sur les six tâches du tamis v3 ; règle de décision inchangée ; plafond de 80 exécutions cloud ; préalables (PAT-120 fusionné, sauvegarde de l'état Foundry de l'opérateur) ; limites énoncées en section 5. **Fixé par le coordinateur avant tout essai, à confirmer par le mainteneur** : le retour donné au correcteur après un refus du juge (section 3.1 : au plus 20 noms de tests, message coupé à 300 caractères) et le nombre de corrections (2 au plus par tentative).

## 1. Origine

La comparaison v3 ([`pat-19-exploration-results-v3.md`](pat-19-exploration-results-v3.md)) s'est conclue `inconclusive` / `keep_cloud_insufficient_evidence` : A 0 tâche acceptée sur 6, L 0 sur 6, E 1 sur 6. Ce que les données montrent : le bras de référence n'a rien fait accepter, donc l'effet aval de l'exploration n'a pas pu être mesuré ; sur les 32 refus du juge de la comparaison, aucun tour de correction n'a changé un seul compte de tests (le correcteur reçoit « refusé » et les décomptes, pas les tests qui échouent) ; 7 drapeaux de contamination, tous de la forme « un bras cloud a nommé la racine de travail du lanceur ». La cause de ces refus et la part de ces drapeaux qui seraient de vrais accès ne sont pas établies par les données. La v4 corrige ces deux points de l'instrument et rejoue la mesure aval, sans rien présumer de son issue.

## 2. Hérité de la v3 par référence (inchangé)

Tout ce que [`pat-19-protocol-v3.md`](pat-19-protocol-v3.md) gèle, et par elle la v2, sauf les points de la section 3 : consigne d'exploration (texte identique, bornes rendues depuis la configuration), explorateur local en lecture seule (`omp --tools=read,grep,glob`, bundle en lecture seule dans le profil du bac à sable), juge de localisation et vérité terrain versée (**même fichier, même sha256**), plafond de 10 fonctions, « refusé ou contaminé compte 0 », règle de comparaison (**acceptation de L ≥ acceptation de A et prime par tâche acceptée de L ≤ 0,85 × celle de A, compatibilité requise ; une donnée absente n'est jamais zéro ; preuve insuffisante = conserver le cloud**), revue, bornes d'exécution, machine dédiée (2 Gio / 35 %), enveloppe, registre, reprise, règle de rechargement (`exploration.one_task_per_launch`, section 4 de la v3), audit de contamination (sauf ce que dit la section 3.2). Les règles posées dans l'en-tête de la v2 et validées le 2026-10-07 restent telles que la v3 les consigne.

## 3. Ce qui change

| Élément | v3 | v4 |
| --- | --- | --- |
| Retour au correcteur après un refus du juge | « refusé » et les décomptes | **+ noms et messages des tests cachés en échec** (3.1) |
| Racine de travail d'une tentative | dossier commun à toutes les tentatives | **racine privée par tentative** (3.2) |
| Bras | A, L, E | **A et L** (pas de bras Haiku E ; pilote `cloud_explorer_economy` non déclaré) |
| Candidat | deux, tamis complet requis | **un seul, fixé** : `qwen3.6-35b-a3b-mlx-4bit` (mêmes clé, empreintes et commande de chargement épinglées), **pas de nouveau tamis** (`screen-exploration` est refusé) |
| Tâches de la comparaison | les six tâches « comparaison » du manifeste (26, 38, 25, 42, 33, 37) | **les six tâches du tamis v3** : PR 30, 83, 27, 24, 48, 19 (`exploration.comparison_task_group: screening`) |
| Plafond d'exécutions cloud | 120 | **80** (2 bras × 6 tâches × au plus 6 exécutions = 72) |

Inchangés et rappelés : bornes d'exploration 60 étapes / 15 minutes ; l'exploration est **rejouée à neuf** dans la comparaison, comme dans la comparaison v3 ; rechargement du modèle avant chaque tâche.

### 3.1 Retour au correcteur (valeurs du coordinateur, à confirmer par le mainteneur)

Après un verdict `REFUSED` du juge mécanique, le fichier de retour du correcteur ajoute, pour chaque test caché échoué ou en erreur : son nom (`classname::nom` du rapport junit) et son message d'échec, **au plus 20 tests**, chaque message **coupé à 300 caractères** (espaces réduits, chemin du bundle remplacé par `<bundle>`), et le décompte des tests non montrés. **Jamais** le code source d'un test, **jamais** son chemin sur le disque. **Même retour pour les deux bras** (même code). **Deux tours de correction au plus** par tentative (inchangé de la v3 : `max_correction_rounds` 2). Un refus sans liste de tests (délai, fichiers du harnais modifiés, défaut du candidat) n'ajoute rien. Limite : le message junit est celui de pytest, il peut citer l'expression d'une assertion (donc une partie de ce que le test appelle) ; ce n'est pas le code du test mais ce n'est pas rien. Le retour après un `BLOCK` de la revue est inchangé.

### 3.2 Racine privée par tentative

Chaque tentative (implémenteur, correction, relecteur, exploration) est construite dans `<racine de travail>/private-<tentative>/<tentative>/` : le parent du dossier de tentative ne contient **que** cette tentative, donc `ls ..` et `ls ../..` depuis le bundle ne montrent aucune autre tentative et ne déclenchent plus de drapeau. **L'audit des accès hors du bundle n'est pas affaibli** : la racine de travail elle-même et toute autre tentative sous elle (chemin relatif ou absolu) sont des racines sensibles ; lire une autre tentative, les tests cachés, le fichier de vérité, `~/.config` ou le reste de ce que l'audit relève reste relevé. Les tentatives sont jetées après usage, la racine privée avec elles.

## 4. Préalables

PAT-120 est fusionné (état Foundry isolé des bras cloud, arrêt si le `registry.json` réel change). **Avant tout lancement**, l'opérateur sauvegarde, hors du dépôt, l'état Foundry aux **deux** emplacements du registre (`~/.config/foundry` et `$FOUNDRY_DATA` quand il l'exporte) : [`pat-19-v4-operator.md`](pat-19-v4-operator.md). Le lanceur ne charge aucun modèle ; il ne lance rien sans enveloppe valide (plafond recommandé : 80 exécutions cloud).

## 5. Limites

- **Six tâches** : la comparaison ne mesure rien d'autre que ces six tâches ; elle ne dit **rien** des autres familles de tâches.
- **Tâches déjà vues par l'explorateur local lors du tamis v3** (explorées localement, avec le même modèle, mais jamais jouées en aval) : le modèle et le réglage n'ont pas été choisis sur un effet aval, mais la comparaison n'est pas faite sur des tâches vierges pour l'explorateur.
- **Pas de bras Haiku** : la v4 ne dit rien d'un explorateur cloud économique, ni de l'exploration cloud en général.
- Les changements de l'instrument (retour de test, racine privée) valent pour les deux bras mais **rendent la v4 non comparable à la v3 tâche à tâche** ; l'effet de chacun, pris seul, n'est pas mesuré.
- Aucune promotion, aucun modèle ou profil local activé : la campagne v4 conclut seulement sur l'usage 2 (exploration), comme la v2 et la v3.

## 6. Statut documentaire (AGENTS.md R5)

Mis à jour : ce fichier, [`pat-19-v4-operator.md`](pat-19-v4-operator.md) et `pat-19-v4-operator.sh`, la section « Protocole v4 » et l'audit de contamination de [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md) (clés `correction_feedback`, `isolation.private_attempt_root`, `exploration.comparison_task_group`, `exploration.fixed_candidate`), `pat-19-campaign-v4.json`, le CHANGELOG ; remarques de PAT-120 : `pat-19-v3-operator.md` (sauvegarde des deux emplacements, formulation « bras cloud ») et la ligne R5 du lanceur. Non appliqué mécaniquement, dit explicitement : le rechargement du modèle (attesté par l'opérateur) et la sauvegarde de l'état Foundry (faite par l'opérateur). Le détecteur d'artefacts de FOUNDRY-123 n'est pas livré.
