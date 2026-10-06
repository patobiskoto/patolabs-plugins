# PAT-19 — Décision après le tamis local, version 1

PAT-110. Cadre : PAT-ADR-0015 (coût net par tâche acceptée, trois verdicts séparés, aucune promotion),
FOUNDRY-ADR-0019 (comparaison bornée, règle fixée d'avance, arrêt séquentiel). Preuves :
[`pat-19-screening-results-v1.md`](pat-19-screening-results-v1.md) et les pièces brutes de
[`pat-19-runs/screen-1/`](pat-19-runs/screen-1/) ; protocole gelé : [`pat-19-protocol-v1.md`](pat-19-protocol-v1.md) ;
lanceur : [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md). Ce document ne modifie aucune règle ni coordonnée du
protocole et n'active rien.

Décision prise par le mainteneur le 2026-10-06 ; le présent document la consigne et cite les preuves.

## Usage 1 : implémentation autonome d'un ticket par un modèle local (Eiffel local)

**Décision : conserver le cloud.** Ce n'est pas un abandon du local en général, et ce n'est pas une adoption.

Les trois verdicts, séparés (PAT-ADR-0015), avec leur source dans le rapport de résultats :

- **Compatibilité : remplie.** Le critère pré-enregistré par la machine (aucun arrêt par signal externe, swap
  supplémentaire sous 10 Gio) est rempli : swap supplémentaire maximal +8,1 Gio, `ended_by_external_signal` faux sur
  les 30 enregistrements ; les cinq candidats exécutent le protocole sous le harnais réel.
- **Qualité : échec au seuil du tamis.** 0 tâche acceptée sur 30 (5 candidats x 6 tâches) ; 0/6 < 2/6 pour chaque
  candidat. La règle de la section 4 du protocole arrête la campagne sur « conserver le cloud » pour cet usage. La
  comparaison au cloud n'a pas été mesurée (`cloud_executions` = 0, aucune référence cloud sur ces tâches).
- **Économie : non mesurée** (pas de comparaison, aucun coût cloud).

Limites qui bornent la portée de cette décision (rapport, section « Limites ») : machine non dédiée (condition de la
section 8 non remplie, swap de 12 à 30 Go), donc l'effet sur l'issue des 19 tentatives bornées en temps est inconnu ;
l'origine des échecs (modèle ou harnais) est inconnue ; 6 tâches ne sont pas une preuve générale. La décision porte
donc sur : « aucun candidat n'a franchi le seuil pré-enregistré dans ces conditions », pas sur « aucun modèle local ne
peut y arriver ».

Options écartées pour cet usage :

- **Extension à 12 tâches sous le même protocole** : écartée. Sous le même protocole, la règle d'arrêt
  séquentiel est déjà déclenchée ; ajouter des tâches ne changerait pas la règle et dépenserait du temps machine pour
  un résultat déjà tranché.
- **Adoption limitée** : écartée, faute de franchir le seuil de qualité.
- **Abandon du local en général** : écarté ; les preuves portent sur un usage, pas sur tous.

**Aucune adoption.** Aucun modèle local, aucun profil, aucun défaut n'est activé ; aucune nouvelle ADR n'est ouverte.
Si une adoption limitée était proposée un jour (pour un autre usage), elle devrait passer par une nouvelle ADR, un
profil exact, une activation opt-in et un retour arrière vérifié (critère 2 de PAT-110) ; rien de tel n'est engagé ici.

## Usage 2 à qualifier : exploration en lecture seule pour un implémenteur cloud (Lupin local)

**Décision : qualifier cet usage ensuite, sous un protocole v2** (PAT-114 le gèle, PAT-115 l'exécute), dans le cadre de
PAT-ADR-0015 : tamis local sans cloud, puis effet en aval en prime nette par tâche acceptée.

Justification (raisonnement, **pas une preuve d'un résultat**) : l'état de l'art (littérature de routage et de
systèmes hybrides) place les petits modèles locaux sur du travail borné, à contexte court, vérifiable et surtout en
lecture. Les modes d'échec observés en v1 vont dans le même sens : croissance du contexte jusqu'à des millions de tokens
d'entrée (2,0 M à 13,1 M par candidat), bornes de temps et d'étapes atteintes. Ces observations motivent le choix de
l'essai ; elles ne disent rien sur la réussite de l'usage 2, qui reste à mesurer.

## Usage possible plus tard, non ouvert : compression des sorties d'outils

Compression des sorties d'outils (journaux de tests et de CI) avant envoi au cloud. Non ouvert : aucun ticket, aucun
protocole, aucune mesure. À reconsidérer après le verdict de PAT-115.

## Jalons de PAT-87 (critère 3 de PAT-110)

| Jalon | Statut | Motif |
| --- | --- | --- |
| PAT-88 (contrat provider et runtime local) | **Reporté** jusqu'au verdict de PAT-115 | Pas de preuve qu'un usage local soit retenu ; aucun abandon. |
| PAT-89 (observation des ressources) | **Reporté** jusqu'au verdict de PAT-115 | Leçon à reprendre : le run v1 a montré que l'admission des ressources compte (machine non dédiée, swap de 12 à 30 Go). |
| PAT-90 (routage local-first) | **Reporté** jusqu'au verdict de PAT-115 | Le périmètre prévu « Eiffel local pour les tâches bornées » n'est pas soutenu par les preuves v1 et reste **non confirmé** ; la partie « Lupin local » dépend de la v2. |
| PAT-91 (qualification de bout en bout) | **Reporté** jusqu'au verdict de PAT-115 | Dépend des précédents. |

Aucun de ces jalons n'est abandonné ni confirmé tel quel ; ils sont tous reportés au verdict de PAT-115.

## État de PAT-19 (critère 4 de PAT-110)

PAT-19 reste ouvert (v2 en cours). Le rapport final de la v1 est
[`pat-19-screening-results-v1.md`](pat-19-screening-results-v1.md), versé au dépôt avec ses pièces brutes. Les critères
d'acceptation propres à PAT-19 ne sont **pas** cochés par cette décision : ils se cocheront selon les preuves, pas
par narration.

## Statut documentaire (R5)

Artefact ajouté : ce document ; liens ajoutés dans `pat-19-screening-results-v1.md` et `pat-19-launcher-v1.md`, ligne
dans le CHANGELOG. Aucun verbe ni option de `foundry_cli.py`, clé de configuration, constante publique ou table de
routage n'a changé ; aucune coordonnée du protocole v1 modifiée ; aucun code.
