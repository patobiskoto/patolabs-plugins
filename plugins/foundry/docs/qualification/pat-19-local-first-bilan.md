# PAT-19 — Bilan local-first, versions 1 à 5

PAT-127, pour l'Epic PAT-87. Ce document rassemble ce que les campagnes v1 à v5 ont établi, ce qu'elles n'ont pas établi, ce
qu'il faudrait pour trancher, et ce que cela change pour PAT-87 à PAT-91 **selon ce que disent les documents du dépôt**.
Il ne décide rien à la place du mainteneur, ne promeut rien, n'active aucun modèle ni profil local et n'annonce aucun gain de
facture (PAT-ADR-0015 : preuve insuffisante = conserver le cloud, aucune promotion ; FOUNDRY-ADR-0007 : aucun rôle local dans le
produit). Sources : [`pat-19-decision-v1.md`](pat-19-decision-v1.md), [`pat-19-screening-results-v1.md`](pat-19-screening-results-v1.md),
[`pat-19-exploration-results-v2.md`](pat-19-exploration-results-v2.md), [`-v3`](pat-19-exploration-results-v3.md),
[`-v4`](pat-19-exploration-results-v4.md), [`-v5`](pat-19-exploration-results-v5.md),
[`pat-19-audit-replay-v4.md`](pat-19-audit-replay-v4.md), [`pat-19-v5-pilots-evidence.md`](pat-19-v5-pilots-evidence.md),
[`pat-19-launcher-v1.md`](pat-19-launcher-v1.md). Les chiffres sont repris de ces documents (recalculés sur les pièces versées
par leurs auteurs) ; je n'ai pas relu les Epics du tracker, donc tout ce qui touche à l'état des tickets PAT-87 à PAT-91 hors des
documents est **inconnu**.

## Ce qu'il faut garder en tête

Douze tâches du même dépôt, dont plusieurs modifient le même fichier, rejouées d'une campagne à l'autre et déjà vues par
l'explorateur local ; un seul candidat local (qwen3.6-35b-a3b-mlx-4bit) retenu en v3 sur le score de six d'entre elles ; une seule
machine, un seul moteur local ; des instruments qui ont changé à chaque version (comparabilité entre versions non assurée). **Six, dix
ou douze tâches ne sont pas une preuve générale** : chaque ligne ci-dessous vaut pour ces tâches et ces conditions.

## Synthèse par rôle

| Rôle ou question | Établi | Pas établi |
| --- | --- | --- |
| Implémentation autonome d'un ticket par un modèle local | v1 : 0 tâche acceptée sur 30 (5 candidats × 6 tâches), en conditions non dédiées ; décision « conserver le cloud » pour cet usage | l'origine des échecs (modèle ou harnais) ; ce qu'un modèle plus grand, une machine dédiée ou un autre harnais donnerait |
| Exploration locale en lecture seule, **localisation** | v2 : 23 tentatives sur 30 coupées par une borne, seuil non atteint ; v3, bornes élargies : le tamis passe, qwen3.6 retenu ; v4 et v5 : sur les explorations scorées, rappel de fichiers 1,0 (4 sur 4, puis 9 sur 9), rappel de fonctions 0,625 puis 0,778 | la généralisation hors de ces tâches ; la stabilité d'un passage à l'autre ; la part du contexte observé (262 144) et des bornes |
| Exploration locale, **effet aval** (prime par tâche acceptée A contre A + L) | v3 inconclusif, v4 inconclusif, v5 `keep_cloud` de justesse (rapport 0,8579 pour un seuil de 0,85, acceptation 5 contre 5) | que L soit moins cher ou meilleur ; l'effet de l'exploration seule ; la reproductibilité |
| Retour des tests cachés au correcteur (instrument) | v3 : jamais un compte de tests changé sur 32 refus ; v4 : 7 des 12 comparaisons de tours changent les comptes ; v5 : 17 des 30 tours après un refus changent les comptes, 11 arrivent à 0 échoué, toutes les acceptations de L viennent après au moins un tour de correction | si l'effet est une meilleure correction ou un ajustement aux valeurs attendues du message (pas de bras sans retour) |
| Exploration par un explorateur cloud économique (bras E de la v3, Haiku 4.5) | v3 : une tâche acceptée sur six pour E, 567 615 à 1 539 306 tokens premium par exploration ; rappel de fonctions 0,792 contre 0,958 pour L sur ces six tâches | ce qu'un autre modèle économique (Haiku 5.5) donnerait ; l'effet aval |
| Machine, compatibilité | v5 : 24 préflights acceptés, mémoire libre minimale 58 %, supplément de swap 0 MiB, aucun signal externe ; OrbStack et ChatGPT doivent être arrêtés pour satisfaire le préflight | le temps de calcul local, la mémoire et l'énergie ne sont pas chiffrés dans la prime |
| Autres usages (compression de sorties d'outils, relecteur local, etc.) | | non ouverts : aucun protocole, aucune mesure dans les documents |

## Ce qui est établi, par campagne

**v1 — implémentation autonome (PAT-110).** 30 tentatives, 0 acceptée ; compatibilité machine remplie, qualité en échec au seuil
du tamis, économie non mesurée (aucune référence cloud). Décision du mainteneur du 2026-10-06 : conserver le cloud pour cet usage,
sans adoption ni abandon du local en général. Limites dites alors : machine non dédiée (swap de 12 à 30 Go), origine des échecs inconnue.

**v2 — tamis de localisation (PAT-115).** 5 candidats × 6 tâches, bornes de 10 minutes et 25 étapes : 23 tentatives sur 30 coupées par
une borne (15 de temps, 8 d'étapes), 7 réponses dans les bornes dont 4 rapports non vides qui nomment tous le bon fichier ; meilleur
rappel moyen de fonctions 0,25 (seuil 0,5), précision de fichiers insuffisante : arrêt « conserver le cloud » pour ce tamis. Le résultat du
dernier candidat est mêlé à une intervention d'opérateur.

**v3 — tamis élargi et première comparaison (PAT-117).** Bornes portées à 900 s et 60 étapes, une tâche par lancement, rechargement du modèle
avant chaque tâche. Tamis : les deux candidats passent les seuils, qwen3.6 retenu (rappel de fonctions 0,639, précision de fichiers 0,958,
rappel de fichiers 1,0, 0 refus). Comparaison A / L / E sur six tâches : A 0 sur 6, L 0 sur 6, E 1 sur 6 ; trois tâches de A jamais jugées
(drapeaux de contamination) ; **`inconclusive`**. Un bras cloud a écrasé le registre Foundry du mainteneur en lançant les tests du dépôt
(corrigé ensuite par PAT-120 ; l'effet sur les issues est inconnu).

**v4 — comparaison A / L, instrument réparé en partie (PAT-122).** 6 tâches : A 1 sur 6, L 0 sur 6, **8 enregistrements sur 32
contaminés** dont 5 erreurs de l'audit selon un diagnostic indépendant ; **`inconclusive`**. Le retour de tests commence à changer des
comptes. L'audit est réparé ensuite hors campagne (PAT-123), un bac à sable natif est ajouté aux bras cloud (PAT-124).

**v5 — comparaison A / L sur les 12 tâches, audit en journal sous bac à sable natif (PAT-126, PAT-127).** 10 tâches décidées dans les
deux bras, 5 acceptées par bras, rapport de prime 0,8579 pour un seuil de 0,85 : **`keep_cloud`**, l'économie échoue de 14 485 tokens par
tâche acceptée. Les deux explorations perdues (chemin inexistant tapé par le modèle) ont retiré 2 tâches sur 12 de la comparaison sans coûter
de token. Détails, tables et limites : [résultats v5](pat-19-exploration-results-v5.md).

## Ce que l'ensemble permet de dire, et de ne pas dire

Permet de dire :

- Sur ces tâches, l'explorateur local localise bien les **fichiers** (rappel 1,0 sur 13 explorations scorées des v4 et v5) et moyennement les
  **fonctions** (0,778 sur 9 scorées en v5) ; il lui arrive d'échouer pour des raisons sans rapport avec la localisation (coupure à 900 s sur PR 30,
  chemin mal tapé sur PR 42 et PR 33, ce dernier compté contaminé par l'audit).
- Sur les 12 tâches, l'ajout du rapport local n'a pas fait baisser l'acceptation sur D (5 contre 5) et a baissé la prime totale de 1 304 381
  tokens sur D (7 874 314 contre 9 178 695, tokens de facturation non pondérés), mais **pas assez pour franchir le seuil pré-enregistré
  par tâche acceptée**, et les tâches acceptées ne sont pas les mêmes (4 différences sur 10). La différence de prime sur D se trouve surtout
  sur l'implémenteur (1 256 825 tokens de moins pour L), partiellement compensée par plus de relecture ; ce sont des lectures de fichiers,
  non une explication causale.
- Le retour de tests cachés au correcteur change les comptes de tests dans une partie des tours et aide à terminer des tâches (toutes les
  acceptations de L et 4 des 6 de A en v5 viennent après un tour de correction) ; il peut contenir des valeurs attendues (v4 §3.1) : cela ne
  prouve pas une meilleure correction.

Ne permet pas de dire :

- que le local est « presque aussi bon » ou « moins cher » : le critère d'économie a échoué, de peu, sur une mesure mince (10 tâches, 5
  acceptations par bras, aucun essai répété, sensibilité de l'étiquette à une seule tâche : voir [v5](pat-19-exploration-results-v5.md),
  « Lectures hors règle ») ;
- qu'un gain de facture existe ou n'existe pas : le temps et la mémoire du côté local ne sont pas chiffrés, la prime inclut le relecteur Opus
  (27 à 34 % de la prime sur D) et traite un token de cache lu comme un token généré ;
- que l'issue vaut pour d'autres dépôts, d'autres familles de tâches, d'autres candidats locaux, d'autres machines.

## Ce qui n'est pas établi (liste)

- L'effet causal du rapport d'exploration sur le coût cloud (pas de répétition, pas de bras sans retour de tests, ensembles de tâches
  acceptées qui diffèrent).
- La variance d'un passage à l'autre pour une même tâche et un même bras.
- L'indépendance des tâches (les douze ont été jouées plusieurs fois et vues par l'explorateur ; neuf modifient `tests/test_linear_tracker.py`).
- Le prix du côté local (temps de calcul, 2 508 s d'exploration en v5, mémoire, énergie, arrêt d'OrbStack).
- Le comportement avec un explorateur cloud économique plus récent (Haiku 5.5 demande Claude Code 2.1.293 ou plus ; la version de la machine est 2.1.285 ; une copie
  2.1.293 serait livrée avec l'application de bureau [observation du coordinateur, à consigner pour PAT-125, sans décision]).
- L'origine des échecs de l'implémentation locale autonome (v1).
- Le comportement des bras sous `dontAsk` et le bac à sable natif sur des tâches variées (appels refusés, tests du dépôt qui touchent le
  home et échouent sous le bac à sable : 49 appels refusés et 10 enregistrements avec écho de `config.env` en v5).
- La non-comparabilité entre versions : v5 n'est pas comparable à v4 (bac à sable, `dontAsk`, 12 tâches, audit révisé).

## Ce qui permettrait de décider (options, sans choix)

Chaque option se lit contre une lacune ci-dessus ; aucune n'est recommandée ici.

1. **Plus de tâches, et indépendantes** (autres dépôts ou familles de tickets) : agit sur l'indépendance et la généralité. Coût :
   constitution et gel d'un corpus, vérité terrain, temps machine. Ne change pas la règle d'une campagne gelée (une nouvelle version s'écrit).
2. **Répéter la même campagne** (mêmes 12 tâches, plusieurs passages) : agit sur la variance et sur la marge de 0,93 % ; le coût premium d'un
   passage a été de 19 308 267 tokens en v5 et de 74 exécutions cloud.
3. **Un bras cloud moins cher** (Haiku 5.5 comme explorateur, ou comme implémenteur) : demande Claude Code 2.1.293 ou plus ; en v3 un
   explorateur Haiku 4.5 a coûté 567 615 à 1 539 306 tokens premium par exploration, avec une prime dominée par les lectures de cache.
4. **Chiffrer le côté local** (temps, mémoire, énergie, coût d'immobilisation de la machine) pour que « prime nette » soit une grandeur
   complète ; la règle actuelle ne compte que les tokens premium.
5. **Cibler les rôles où le cloud dépense en exploration** plutôt que mesurer l'exploration seule : en v5, la prime de A se répartit en
   implémenteur 39 %, correcteur 31 %, relecteur 30 % (12 tâches) ; l'exploration n'est pas isolée dans ces classes ; une mesure du poids de la
   lecture de dépôt dans l'implémenteur cloud est possible sur les flux existants (hors dépôt) sans nouvelle campagne.
6. **Réduire le bruit d'instrument** avant de remesurer : l'audit (chemin inexistant tapé par le modèle et lu comme un accès par l'outil
   `read`), les tests du dépôt qui touchent le home sous le bac à sable, la veille du dossier temporaire partagé. Chaque point passe par `foundry:intake`.
7. **Ne pas mesurer davantage** et en rester à « conserver le cloud » pour l'exploration, faute de preuve : la règle (PAT-ADR-0015) le permet et
   n'engage rien.

## Implications pour PAT-87 à PAT-91 (limitées à ce que disent les documents)

- La décision v1 (2026-10-06, [`pat-19-decision-v1.md`](pat-19-decision-v1.md)) a **reporté** PAT-88 (contrat provider et runtime local), PAT-89
  (observation des ressources), PAT-90 (routage local-first) et PAT-91 (qualification de bout en bout) au verdict de PAT-115 ; aucun
  n'était abandonné ni confirmé. Elle notait que le périmètre prévu de PAT-90 « Eiffel local pour les tâches bornées » n'est pas soutenu par les
  preuves v1, et que la partie « Lupin local » (exploration) de PAT-90 dépend de la v2.
- Depuis, les documents du dépôt apportent ceci : l'usage exploration (v2 à v5) n'a **pas** produit de preuve de retenue du local (v2 non atteint, v3 et v4
  inconclusifs, v5 `keep_cloud` de justesse) ; il apporte un candidat qui localise bien les fichiers ; l'admission des ressources compte
  (préflight de machine dédiée refusé en v4 par OrbStack, arrêt d'OrbStack et de ChatGPT pour la v5, mémoire libre relevée), ce qui touche
  l'objet de PAT-89 ; le chargement des modèles est resté manuel et couvert par un mandat de bloc, sans contrat provider (objet de PAT-88).
- Ce que ces documents **ne disent pas** : l'état courant de PAT-87 à PAT-91 dans le tracker, une décision de reprendre ou d'abandonner
  l'un d'eux, ni une V1.1 : **inconnu**. Aucune ADR n'est ouverte ni modifiée par ce bilan.

## Statut documentaire (R5)

Artefact ajouté : ce document (avec les résultats v5 et `pat-19-runs/x5compare-1/`). Aucun verbe ni option de `foundry_cli.py`, clé de
configuration, constante publique ou table de routage n'a changé ; aucun code. Détecteur FOUNDRY-123 non livré : statut affirmé ici,
vérifié en revue.
