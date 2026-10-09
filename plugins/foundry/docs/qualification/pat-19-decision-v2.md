# PAT-19 — Décision par usage qui conclut la qualification, version 2

PAT-130. Cadre : PAT-ADR-0015 (verdicts par usage, trois verdicts séparés, preuve insuffisante = conserver le cloud, aucune
promotion par la qualification, un protocole gelé ne se modifie pas), FOUNDRY-ADR-0019 (comparaison bornée, règle fixée d'avance),
FOUNDRY-ADR-0007 (aucun rôle local dans le produit). Ce document **consigne** les décisions par usage de la qualification PAT-19
(protocoles v1 à v5 et suites du 2026-10-09) pour que l'Epic PAT-87 puisse s'appuyer sur un texte du dépôt et non sur une
conversation. Il ne modifie aucune règle, coordonnée, configuration, résultat ni verdict gelé, ne recalcule ni ne requalifie
rien, n'active aucun modèle ni profil local, n'ouvre aucune ADR et **ne décide pas la clôture de l'Epic**. Il complète
[`pat-19-decision-v1.md`](pat-19-decision-v1.md) (usage 1, 2026-10-06) sans la remplacer.

Légende. **[fichiers]** : lu ou recalculé sur un document ou une pièce versée au dépôt (chiffre repris du document cité, qui prime).
**[coord.]** : rapporté par le coordinateur, hors dépôt, non vérifiable depuis le dépôt (mots du mainteneur dans la conversation,
état du tracker, brouillon non fusionné). **[hypothèse]** : lecture non établie. **[inconnu]** : non su. Seuls les mots entre
guillemets français sont attribués au mainteneur ; ce que le coordinateur a fixé, proposé ou recommandé est dit comme tel.

## Les décisions, dans l'ordre

| Date | Qui | Quoi | Source |
| --- | --- | --- | --- |
| 2026-10-06 | mainteneur | usage 1 : conserver le cloud pour l'implémentation locale autonome, sans adopter ni abandonner le local en général | [`pat-19-decision-v1.md`](pat-19-decision-v1.md) [fichiers] |
| 2026-10-08 | mainteneur | après lecture du bilan v1 à v5, a demandé « En résumé la 1.1.0 il en reste quoi de possible ? », puis « Ok donc on fixe les objectifs de la 1.1.0 au regarde de ce qui reste en backlog et de ce que nous pourrions travailler comme nouvelle piste stp » | [coord.] |
| 2026-10-08 | coordinateur | a proposé un recadrage de l'Epic PAT-87 dont le premier objectif était : clore la qualification locale par usage (implémentation locale autonome : conserver le cloud ; exploration locale : conserver le cloud, gain non démontré), et qui reportait PAT-88 à PAT-91, PAT-17 et PAT-18 comme conditionnels à un profil local qualifié | [coord.] |
| 2026-10-08 | mainteneur | a répondu « Ok go et va jusqu'au test et validation ». L'Epic PAT-87 a été réécrit en conséquence le même jour (objectif : réduire le travail premium par tâche acceptée, avec preuve mesurée ; le local est un moyen parmi d'autres) | [coord.] ; contenu de l'Epic non relu ici [inconnu] |
| 2026-10-09 | mainteneur | sur Haiku 5.5 : « Ok j'autorise le second essai » et « Médium très bien » : décision distincte, côté cloud, voir plus bas | [`pat-125-haiku-55-promotion.md`](pat-125-haiku-55-promotion.md) [fichiers] |
| 2026-10-09 | coordinateur, puis mainteneur | compression : le coordinateur n'a lancé ni pilote ni campagne alors qu'ils étaient autorisés, a rapporté la table de tailles ci-dessous et recommandé d'abandonner cette piste sur ce corpus ; parmi trois options (abandonner sur ce corpus ; recadrer vers des sorties vraiment longues comme des journaux de CI ; jouer quand même le protocole validé le 2026-10-07), le mainteneur a répondu « Ok go » au message dont la recommandation était la première. **Consigné comme : le mainteneur a approuvé la recommandation du coordinateur**, rien de plus | [coord.] |

Les mots du mainteneur des 2026-10-08 et 2026-10-09 (compression) ne figurent pas dans le dépôt : ce sont les mots que rapporte
le coordinateur, comme les documents précédents le font pour les accords de conversation.

## Usage 1 : implémentation autonome d'un ticket par un modèle local

**Décision : conserver le cloud** (mainteneur, 2026-10-06, déjà consignée [fichiers]). Preuve : v1, 0 tâche acceptée sur 30 tentatives
(5 candidats × 6 tâches). Lectures séparées, telles que la décision v1 les rend :

- **Compatibilité** : remplie.
- **Qualité** : échec au seuil du tamis (0/6 < 2/6 pour chaque candidat).
- **Économie** : non mesurée (aucune référence cloud).

Inconnu : l'origine des échecs (modèle ou harnais) ; ce que donnerait un modèle plus grand, une machine dédiée ou un autre harnais.
La décision porte sur « aucun candidat n'a franchi le seuil pré-enregistré dans ces conditions », pas sur « aucun modèle local ne
peut y arriver ». Aucune nouvelle mesure n'a été faite sur cet usage depuis.

## Usage 2 : exploration locale en lecture seule pour un implémenteur cloud

**Décision (exprimée par le coordinateur, sur son recadrage approuvé le 2026-10-08 par « Ok go et va jusqu'au test et validation ») : conserver
le cloud, gain non démontré** [coord.]. Ce n'est pas « le local est moins bon » : c'est que la preuve exigée par PAT-ADR-0015 n'a pas
été apportée. La règle de PAT-ADR-0015 (preuve insuffisante = conserver le cloud) donne le même résultat sans décision humaine
supplémentaire [fichiers].

Preuves [fichiers], campagnes v2 à v5 (non comparables entre elles : instruments changés à chaque version, voir le
[bilan](pat-19-local-first-bilan.md)) :

| Version | Issue | Source |
| --- | --- | --- |
| v2 (tamis) | seuil non atteint ; 23 tentatives sur 30 coupées par une borne | [`pat-19-exploration-results-v2.md`](pat-19-exploration-results-v2.md) |
| v3 | tamis passé (qwen3.6-35b-a3b-mlx-4bit retenu), comparaison `inconclusive` | [`pat-19-exploration-results-v3.md`](pat-19-exploration-results-v3.md) |
| v4 | `inconclusive` | [`pat-19-exploration-results-v4.md`](pat-19-exploration-results-v4.md) |
| v5 | `keep_cloud` : 10 tâches appariées, 5 acceptées par bras, prime par tâche acceptée L/A 0,8579 pour un seuil de 0,85 | [`pat-19-exploration-results-v5.md`](pat-19-exploration-results-v5.md) |

Erratum de la v5 [fichiers] : lue avec la règle des v2 à v4, la v5 aurait été `inconclusive` ; `keep_cloud` vient du traitement des tâches
indécidées fixé avant la campagne. Les deux étiquettes recommandent le cloud. Aucun verdict n'est modifié ici.

Les trois lectures, séparées (PAT-ADR-0015) :

- **Compatibilité** : remplie en v5 (24 préflights acceptés, supplément de swap 0 MiB, aucun signal externe), au prix d'arrêter
  OrbStack et ChatGPT pour la campagne [fichiers].
- **Qualité** : sur les explorations scorées des v4 et v5, rappel de fichiers 1,0 et rappel de fonctions moyen (0,778 sur 9 explorations
  scorées en v5) ; acceptation égale (5 contre 5 sur D) mais sur des ensembles de tâches qui diffèrent sur 4 tâches sur 10 [fichiers].
- **Économie** : le critère gelé échoue de peu (0,8579 contre 0,85, soit 0,93 % de la ligne) [fichiers]. **Lectures hors règle, sans
  valeur de décision, qui ne remplacent ni ne requalifient le verdict** ([`pat-19-cost-breakdown-v1.md`](pat-19-cost-breakdown-v1.md), PAT-129) :
  pondéré par des prix de liste (poids, pas une facture), le rapport L/A par tâche acceptée sur D est d'environ 1,03 en v5 (1,032) et de
  1,03 à 1,04 sur les 5 tâches communes de la v4 (1,0270 à 5 min, 1,0423 à 1 h pour l'écriture de cache) ; le relecteur Opus pèse 51 à 55 %
  du poids en v5 (48 à 51 % si la lecture de cache de Sonnet 5.5 coûte 0,20 USD par million au lieu de 0,10), l'écriture de cache 56 à 59 %
  [fichiers]. Ces rapports ne distinguent pas L de A avec 5 tâches acceptées par bras.

Instrument : PAT-128 a établi par rejeu pourquoi les deux explorations de la v5 (PR 42 et PR 33) avaient été signalées contaminées (le
modèle local avait tapé un chemin inexistant, l'audit ne reconnaissait pas la forme `Path '<chemin>' not found` de l'erreur de l'outil) et a
ajouté une coordonnée utilisable par un protocole postérieur à la v5 ([`pat-19-audit-replay-v5.md`](pat-19-audit-replay-v5.md)) [fichiers].
Aucun verdict passé n'est recalculé ; l'effet sur le verdict de la v5 est **[inconnu]**. Aucun protocole v6 n'existe.

Ne permet pas de dire : que le local est « moins cher » ou « presque aussi bon » ; qu'un gain ou une perte de facture existe (le forfait est un
abonnement, le temps, la mémoire et l'énergie locaux ne sont pas chiffrés) ; que l'issue vaut pour d'autres dépôts, tâches, candidats ou machines.
**Douze tâches non indépendantes (déjà jouées, vues par l'explorateur, neuf modifient le même fichier de test) ne sont pas une preuve générale.**

## Usage 3 : compression des sorties d'outils en un appel local

**Décision : piste abandonnée sur ce corpus, faute d'objet mesurable.** Origine : recommandation du coordinateur, approuvée par le
mainteneur par « Ok go » (2026-10-09) [coord.]. Rien de plus n'est attribué au mainteneur.

Cet usage n'a **jamais été mesuré** : aucun modèle, aucun pilote, aucune campagne, aucun verdict. Il n'y a donc ni lecture de compatibilité,
ni de qualité, ni d'économie. Ce qui existe est un brouillon : protocole c1 et mode de lanceur sur la branche non fusionnée
`chore/pat-118-geler-le-protocole-de-compression` (commit 2489e7f), rien de gelé, rien de joué.

Taille de la matière réelle (sorties des tests protégés sur le code de base ; octets / tests en échec). **Ces tailles ne sont dans aucun
fichier de la branche principale** : elles viennent du brouillon non fusionné et sont rapportées par le coordinateur [coord.] (relues pour
ce document dans la pièce du commit 2489e7f, sans la verser).

| PR | Octets | Échecs | PR | Octets | Échecs |
| --- | --- | --- | --- | --- | --- |
| 26 | 1 274 | 1 | 38 | 10 835 | 9 |
| 37 | 2 294 | 1 | 19 | 23 896 | 4 |
| 30 | 2 859 | 2 | 25 | 24 078 | 4 |
| 27 | 3 854 | 2 | 24 | 33 026 | 9 |
| 42 | 5 317 | 4 | 83 | 52 188 | 11 |
| 33 | 7 837 | 2 | 48 | 425 688 | 122 |

Total 593 146 octets, médiane 9 336 octets ; la PR 48 porte 71,8 % des octets [coord.]. Pour l'échelle (non décisionnel) : une tentative
cloud de la v5 a consommé une médiane de 240 191 tokens premium (57 tentatives, de 79 182 à 2 015 388) [fichiers : recalculé sur
`pat-19-runs/x5compare-1/results-pat-19-x5compare-1.jsonl`]. L'équivalent en tokens des sorties est **[inconnu]** (aucun décompte exact avec
un tokeniseur) ; il est donc non établi que l'ordre de grandeur de ces sorties compte face à une tentative cloud. La recommandation
du coordinateur se fonde sur ces tailles (médiane de 9 336 octets, une tâche en porte la plus grande part) ; elle ne dit rien d'autres sorties
(journaux de CI longs) ni d'autres corpus.

## Ce qui n'est pas décidé

- **Aucun modèle local, profil local ni nouveau défaut local n'est activé** ; aucune promotion, aucune ADR ouverte ou modifiée
  (PAT-ADR-0015, FOUNDRY-ADR-0007). Si un usage local était un jour proposé, il passerait par une nouvelle ADR, un profil exact, une
  activation opt-in et un retour arrière vérifié.
- **PAT-88 à PAT-91, PAT-17 et PAT-18 restent reportés et conditionnels** à un profil local qualifié [coord.] ; aucun n'est abandonné ni
  confirmé par ce document. Leur jalon commun ne peut pas être levé par l'outillage : à retirer à la main [coord.].
- **La promotion de Haiku 5.5 (PAT-125, PAT-ADR-0016) est une décision distincte, côté cloud** : elle n'est ni une conclusion de cette
  qualification ni une réponse à ses lectures ; elle ne repose que sur le prix catalogue et aucun gain mesuré n'est affirmé
  ([`pat-125-haiku-55-promotion.md`](pat-125-haiku-55-promotion.md)). Les résultats de PAT-19 restent liés à Haiku 4.5.
- **Aucun gain de facture n'est annoncé**, ni aucune perte.
- **La clôture de l'Epic PAT-87 n'est ni décidée ni réalisée ici.**
- Les pistes que les mesures désignent (ci-dessous) ne sont pas décidées.

## Sort des tickets ouverts de l'Epic et du brouillon c1

- **PAT-118** (protocole de compression) : brouillon conservé sur sa branche, non fusionné. Foundry n'a pas d'état « annulé » et l'adaptateur de
  tracker ne peut pas le remettre en backlog : il reste « en cours » dans le tracker jusqu'à ce que le mainteneur l'annule dans Linear [coord.].
- **PAT-119** : non démarré, reste en backlog (même remarque) [coord.].
- **PAT-113** (audit des bras cloud pour le protocole v1) : laissé en backlog. Lecture du coordinateur : la politique d'audit de la v5 le rend en
  grande partie caduc. **Non décidé** par le mainteneur [coord.].
- **Brouillon c1** : non gelé, non joué, non fusionné ; il n'est repris par aucun protocole de ce dépôt.

## Pistes que les mesures désignent, sans les décider

- Le poids du relecteur Opus et de l'écriture de cache dans le coût pondéré par le prix (voir les lectures hors règle de l'usage 2).
- Un explorateur cloud moins cher est maintenant le défaut (Haiku 5.5) ; son effet est **non mesuré**.

Ce sont des pistes de mesure, pas des engagements ; toute idée qui en sort passe par `foundry:intake` (R3).

## Ce qui reste inconnu

- Ce qu'un modèle local plus grand, une machine dédiée ou un autre harnais donneraient en implémentation autonome (v1).
- Si l'écart de la v5 survivrait à une répétition ; la variance d'un passage à l'autre ; l'effet causal du rapport d'exploration sur le coût cloud.
- Le prix du côté local (temps, mémoire, énergie).
- L'effet sur le verdict de la v5 de la forme d'erreur désormais reconnue par l'audit (PAT-128).
- Le poids en tokens des sorties d'outils de la matière de PAT-118 ; l'effet d'un explorateur Haiku 5.5.
- L'état courant du tracker (Epic PAT-87 réécrit, tickets PAT-113/118/119, jalon) : non relu pour ce document.

## Statut documentaire (R5)

Artefact ajouté : ce document ; une ligne de renvoi dans `pat-19-local-first-bilan.md` et dans `pat-19-launcher-v1.md` ; une entrée de
CHANGELOG. Aucun verbe ni option de `foundry_cli.py`, clé de configuration, constante publique ni table de routage n'a changé ; protocoles,
configurations et résultats v1 à v5 inchangés ; aucun code. Détecteur FOUNDRY-123 non livré : statut affirmé ici, vérifié en revue.
