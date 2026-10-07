# PAT-19 — Protocole de qualification local-first, version 3 : exploration en lecture seule, budget élargi

PAT-116. Cadre : PAT-ADR-0015 (exploration évaluée seulement par son effet aval, tamis local sans cloud avec une règle écrite d'avance, coût net par tâche acceptée, une donnée absente n'est jamais zéro, preuve insuffisante = conserver le cloud, aucune promotion), FOUNDRY-ADR-0019 (comparaison bornée, règle fixée d'avance, pas de second cadre), FOUNDRY-ADR-0010 (enveloppe), FOUNDRY-ADR-0015 (coût lu dans les journaux de session), FOUNDRY-ADR-0007 (aucun rôle local dans le produit). Tout changement d'une coordonnée gelée (ci-dessous ou héritée) ouvre une version 4.

**Gelé avant tout essai v3.** Aucune tentative v3 n'a eu lieu. Ce protocole **ne modifie pas** les protocoles v1 et v2, leurs configurations (`pat-19-campaign-v1.json`, `pat-19-campaign-v2.json`) ni leurs résultats ([`pat-19-exploration-results-v2.md`](pat-19-exploration-results-v2.md), `pat-19-runs/`), qui restent gelés et reproductibles.

**Qui a validé quoi, et quand.** Validé par le mainteneur le **2026-10-07** : (a) les valeurs de la v3 ci-dessous (budget 60 étapes et 15 minutes, deux candidats, rechargement du modèle avant chaque tâche) ; (b) les règles posées par le coordinateur dans l'en-tête de la v2, jusque-là « pas encore validées » et désormais **validées** (note de PAT-115) : liste d'autorisation système réduite de la machine dédiée, « exploration coupée par une borne = refus », « durée ≥ borne = refus », « `start_error` = tentative nulle », la conclusion `keep_cloud_insufficient_evidence`, le préflight de départ de la comparaison. Les corrections d'exécution annoncées le 2026-10-06 et les règles validées le 2026-10-06 restent telles que la v2 les consigne.

## 1. Origine

Le tamis v2 ([`pat-19-exploration-results-v2.md`](pat-19-exploration-results-v2.md)) a coupé 23 explorations sur 30 avant qu'elles répondent (15 par le temps, 8 par les étapes) ; les 4 rapports non vides désignaient le bon fichier. Une coupure compte 0 (refus) : le tamis v2 mesurait surtout le budget. La v3 ne change que ce budget.

## 2. Hérité de la v2 par référence (inchangé)

Tout ce que [`pat-19-protocol-v2.md`](pat-19-protocol-v2.md) gèle, sauf les trois points de la section 3 : corpus (6 tâches de tamis, 6 de comparaison jamais jouées), consigne d'exploration (texte identique, les bornes y sont rendues depuis la configuration), explorateur local en lecture seule (`omp --tools=read,grep,glob`, bundle en lecture seule dans le profil du bac à sable), explorateur cloud économique du bras E, juge de localisation et vérité terrain versée (**même fichier, même sha256**), plafond de 10 fonctions, seuils (rappel moyen de fonctions ≥ 0,5, précision moyenne de fichiers ≥ 0,5), règles d'égalité, « refusé ou contaminé compte 0 », comparaison (bras A / L / E sur les 6 tâches de comparaison ; L retenu si acceptation ≥ A et prime par tâche acceptée ≤ 0,85 × A ; compatibilité requise), revue et corrections comme en v1, plafond de 120 exécutions cloud, machine dédiée (2 Gio / 35 %), enveloppe, registre, reprise, audit de contamination.

## 3. Ce qui change

| Élément | v2 | v3 |
| --- | --- | --- |
| Bornes de l'explorateur | 25 étapes, 10 min | **60 étapes, 15 min** (`explorer_max_steps` 60, `explorer_max_seconds` 900) |
| Candidats | cinq | **deux** : `qwen3.6-35b-a3b-mlx-4bit` puis `qwen3-coder-30b-a3b-mlx-4bit` (mêmes clés, empreintes et commandes de chargement épinglées qu'en v1 et v2) ; les deux sont requis pour un tamis complet |
| Chargement du modèle | une fois par campagne (décision ad hoc du coordinateur pour Qwen3-Coder en v2, mémoire libre tombée à 9 %) | **rechargé avant chaque tâche, pour tous les candidats : règle écrite d'avance** (section 4) |

## 4. Règle de rechargement (écrite d'avance)

Le lanceur ne charge aucun modèle (inchangé). La config v3 porte `exploration.one_task_per_launch: true` : `screen-exploration` joue **au plus une tâche non décidée** du candidat par lancement, `compare-exploration` **au plus une tâche de comparaison** (tous ses bras) par lancement, puis sortent en code 0 en écrivant sur la sortie standard `pat19-v3: work_remains=yes` ou `pat19-v3: work_remains=no`. Entre deux lancements, l'opérateur décharge puis recharge le modèle avec la commande épinglée ([`pat-19-v3-operator.md`](pat-19-v3-operator.md) : la boucle exacte). La reprise est inchangée (une tâche décidée est sautée). Le registre montre un préflight par lancement, donc par tâche, et le préflight refuse un autre modèle que celui attendu : **le rechargement par tâche est attesté par le script opérateur et une session par tâche, pas par le lanceur** (le lanceur ne peut pas prouver qu'un rechargement a eu lieu entre deux sessions, seulement qu'un modèle conforme est chargé).

## 5. Limites

- **Le tamis v3 rejoue les six mêmes tâches de tamis que la v2, avec un budget élargi après avoir vu leurs résultats** : il est réglé sur ces tâches. Il ne prouve rien par lui-même ; la preuve retenue est la comparaison aval sur les six tâches de comparaison, jamais jouées (PAT-ADR-0015 : l'exploration ne se juge que par son effet aval).
- Les essais réels des pilotes v2 (explorateur `omp`, explorateur Haiku, [`pat-19-preflight-v2-2026-10-06.json`](pat-19-preflight-v2-2026-10-06.json)) **restent valables** : l'argv et le texte de la consigne sont inchangés, seules les bornes rendues diffèrent.
- Le rechargement avant chaque tâche rend les durées comparables entre tâches mais ne dit rien du comportement d'un modèle resté chargé.
- Aucune promotion, aucun modèle ou profil local activé : une campagne v3 conclut seulement sur l'usage 2 (exploration), comme la v2.

## 6. Statut documentaire (AGENTS.md R5)

Mis à jour : ce fichier, [`pat-19-v3-operator.md`](pat-19-v3-operator.md), la section « Protocole v3 » de [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md) (clé de configuration `exploration.one_task_per_launch`, ligne `pat19-v3: work_remains=…`), `pat-19-campaign-v3.json`, le CHANGELOG. Non appliqué mécaniquement, dit explicitement : le rechargement du modèle (attesté par l'opérateur). Le détecteur d'artefacts de FOUNDRY-123 n'est pas livré.
