# PAT-19 — Résultats du tamis local, version 1

PAT-109. Cadre : PAT-ADR-0015 (coût net par tâche acceptée, trois verdicts séparés, aucune promotion),
FOUNDRY-ADR-0019 (comparaison bornée, règle fixée d'avance, arrêt séquentiel). Protocole gelé :
[`pat-19-protocol-v1.md`](pat-19-protocol-v1.md) (section 4) ; lanceur : [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md) ;
corpus : [`pat-19-corpus-v1.md`](pat-19-corpus-v1.md). Aucune règle ni coordonnée du protocole n'est modifiée par ce
document ; il consigne un essai réel et s'arrête là.

## Résumé

Campagne `pat-19-screen-1`, tamis seul (enveloppe sans cloud), exécutée le 2026-10-06 de 00:52 à environ 09:50.
**0 tâche acceptée sur 30** (5 candidats × 6 tâches de tamis). Aucun candidat n'atteint 2 acceptations sur 6 :
par la règle de la section 4 du protocole, la campagne s'arrête sur « conserver le cloud » pour cet usage
(implémentation autonome d'un ticket). La comparaison n'a pas été lancée ; aucun quota cloud n'a été dépensé
(`cloud_executions` = 0 sur les 30 résultats).

Décision du mainteneur du 2026-10-06, consignée par PAT-110 dans [`pat-19-decision-v1.md`](pat-19-decision-v1.md) : conserver le cloud pour l'implémentation
autonome ; ensuite, un protocole v2 pour l'exploration en lecture seule (PAT-114, PAT-115).

## Conditions de l'essai

- Code : `main` à `879e7b7` (PAT-111 et PAT-112 fusionnés) ; harnais omp 18.6.1 (correction de version validée par
  le mainteneur le 2026-10-06) ; LM Studio 0.4.25+1 ; contexte >= 65 536.
- Chaque candidat chargé avec sa `load_command` épinglée, un à la fois ; préflight vert pour chacun.
- Enveloppe : `envelope-pat-19-screen-1.json`, sans cloud.
- Moteurs (protocole section 4) : Muse Glimmer est un GGUF servi par llama.cpp ; les quatre autres candidats sont
  servis par MLX. La différence de moteur est une variable de plus dans la comparaison entre candidats.
- Écart au protocole section 8 : les chargements de modèles ont été autorisés à l'avance et en bloc par le mainteneur
  le soir du 2026-10-05 (« jusqu'au bout »), et non confirmés un par un comme la section 8 l'exige.
- Empreintes (rapport) : campagne `a678dc67…`, manifeste `8ac65091…`, enveloppe `84aad431…`.

## Résultats par candidat

Recalculés à partir des `results-pat-19-screen-1.jsonl` versionnés (30 enregistrements `attempt`).

| Candidat | Acceptées | Arrêts | Étapes (min–max) | Secondes (somme) | Tokens d'entrée (flux) |
| --- | --- | --- | --- | --- | --- |
| qwen3.8-27b-mlx-6bit | 0/6 | 6 bornes de temps | 9–22 | 7200,2 | 1,98 M |
| qwen3.8-27b-mlx-4bit | 0/6 | 6 bornes de temps | 7–20 | 7200,2 (1) | 2,42 M |
| qwen3.6-35b-a3b-mlx-4bit | 0/6 | 6 bornes d'étapes (40) | 41 (2) | 4033,3 | 13,14 M |
| muse-glimmer-30b-gguf | 0/6 | 3 bornes de temps + 3 arrêts spontanés | 15–20 | 6789,8 | 2,99 M |
| qwen3-coder-30b-a3b-mlx-4bit | 0/6 | 4 bornes de temps + 2 arrêts spontanés | 10–36 | 6825,6 | 3,69 M |

(1) Le rapport mécanique donne 6000,168 s pour ce candidat : il compte 5 tâches, la tentative contaminée (voir
plus bas) en est exclue (écart 1200,035 s). (2) Pour omp, la borne d'étapes est appliquée mécaniquement : le groupe de processus est tué à la borne
(enregistrements qwen3.6 : signal 9, `timed_out` faux, durées 363–1191 s). Seule la borne du harnais neutre est
vérifiée a posteriori. La coupure survient à 41 étapes enregistrées pour une borne de 40 (décalage d'un dans le
lanceur, sans effet sur l'issue ; code non corrigé ici).

Les tokens d'entrée du flux sont de 2,0 M à 13,1 M par candidat : la croissance du contexte domine, l'hypothèse
est que le coût est borné par le préremplissage (prefill), non vérifiée. Les débits de génération et de préremplissage ne sont pas exposés par le
flux d'événements : ils restent inconnus.

Pics de swap par candidat, `peak_swap_used_mib` (MiB) dans `report-pat-19-screen-1.json` : 25311,5 (muse), 30575,0 (qwen3-coder), 25455,5 (qwen3.6), 25511,5 (qwen3.8 4 bits),
25407,69 (qwen3.8 6 bits).

## Juge face à la base sans diff

Comparaison, recalculée sur les fichiers versionnés, des comptes de tests protégés du juge (`judge`) avec
`verification[].without_merged_diff` du manifeste de corpus (`pat-19-corpus-manifest-v1.json`), par tâche :

- **27 tentatives décidées + 1 contaminée** (28 sur 30) ont exactement les comptes de la base sans diff (passés,
  échoués, erreurs, ignorés) : aucun progrès mesurable.
- **Une amélioration partielle** : qwen3.6, PR 83, 12 passés / 6 échoués contre 7 / 11 pour la base. Refusée
  quand même (non acceptée).
- **Une dégradation** : qwen3.6, PR 48, 0 passé / 0 échoué / 1 erreur (collecte cassée) contre 223 / 122 pour la base.

Recalcul : 28 + 1 + 1 = 30.

## Contamination et statut mécanique du rapport

Le rapport mécanique conclut `incomplete_screening` (`selected` null) à cause d'une seule contamination :
qwen3.8-27b-mlx-4bit, PR 48 (PAT-72), un `ls` de la racine de travail du lanceur (dossier `pat19-work` sous le
répertoire temporaire ; seul le dossier de cette tentative y existait), relevé comme ancêtre du bundle. La tentative
est jugée REFUSED quand même (`judge.verdict` REFUSED, `local_outcome` `contaminated`, `accepted` null), donc elle ne peut pas changer l'issue :
une contamination ne peut que retirer une acceptation, jamais en créer. Aucune règle n'est modifiée.

## Limites

- Machine non dédiée : le protocole section 8 fait d'une machine dédiée une CONDITION (« sans autre modèle chargé ni
  autre projet consommateur de mémoire »), qui n'a PAS été remplie : le processus d'un autre projet tenait environ
  18 Go résidents ; le swap est passé d'environ 12 Go à 25–30 Go ; la mémoire libre mesurée est descendue jusqu'à
  9 % (qwen3-coder). Pour les 19 tentatives bornées en temps (6 + 6 + 3 + 4), l'effet sur l'issue est inconnu (la lenteur
  peut expliquer l'absence de progrès). Seuls les 6 arrêts par borne d'étapes de qwen3.6 et les 5 arrêts spontanés
  ne s'expliquent pas par la vitesse.
- Borne de 2 heures par candidat (section 4) dépassée de 0,2 s pour qwen3.8 6 bits et 4 bits (7200,197 s et 7200,203 s).
- Inconnus : l'origine des échecs (modèle ou harnais) est inconnue, le harnais neutre n'ayant pas été joué ; les
  paramètres d'échantillonnage effectifs ne sont pas enregistrés ; les débits de génération et de préremplissage non plus.
- Aucune référence cloud sur ces 6 tâches : leur difficulté n'est pas calibrée.
- 6 tâches ne constituent pas une preuve générale.

## Les trois verdicts (PAT-ADR-0015)

- **Compatibilité** : critère pré-enregistré (lanceur : aucun arrêt par signal externe et swap supplémentaire sous
  10 Go) rempli : swap supplémentaire maximal +8,1 Gio (première tentative qwen3.8 6 bits, 12205,69 -> 20500,12 MiB),
  `ended_by_external_signal` faux sur les 30 enregistrements. Les cinq candidats exécutent le protocole sous le
  harnais réel (appels d'outils fonctionnels, aucune panne de harnais constatée).
- **Qualité** : 0 acceptée sur 30 ; échec au seuil du tamis (0/6 < 2/6 pour chaque candidat) ; comparaison au cloud
  non mesurée (aucune référence cloud sur ces tâches).
- **Économie** : non mesurée (pas de comparaison, aucun coût cloud).

Aucune promotion.

## Pièces versionnées

Dans [`pat-19-runs/screen-1/`](pat-19-runs/screen-1/), copiées telles quelles : `ledger-pat-19-screen-1.jsonl`,
`results-pat-19-screen-1.jsonl`, `report-pat-19-screen-1.json`, `envelope-pat-19-screen-1.json`,
`streams-manifest.json`. Les 30 flux d'événements omp bruts ne sont pas versionnés (49 Mo, ils embarquent des
contenus du dépôt) ; ils restent sur la machine du mainteneur, et `streams-manifest.json` donne leur sha256 et leur
taille. La commande de contamination (`ls` de la racine de travail) et le fait que seul le dossier de cette
tentative y existait ont été lus dans ces flux bruts hors dépôt. L'empreinte de campagne `a678dc67…` a été vérifiée
par le coordinateur : elle égale le sha256 de `pat-19-campaign-v1.json` à `879e7b7`.

## Statut documentaire (R5)

Ce document et les cinq pièces ci-dessus sont les seuls artefacts ajoutés. Aucun verbe ni option de
`foundry_cli.py`, clé de configuration, constante publique ou table de routage n'a changé.
