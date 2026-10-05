# PAT-19 — Protocole de qualification local-first, version 1

Cadre : PAT-ADR-0015 (acceptée le 2026-10-05). Valeurs validées par le mainteneur le 2026-10-05. Tout changement d'une coordonnée gelée ouvre une version 2.

## 1. Question posée

Pour des correctifs bornés de ce dépôt, un parcours « implémenteur local puis validation cloud » coûte-t-il moins de travail cloud premium et pas trop plus de temps, par tâche acceptée, que le parcours cloud actuel, à qualité égale ?

Un usage, une machine, et un seul candidat local dans la comparaison : il est désigné par le tamis local de la section 4. L'exploration (Lupin) n'est pas mesurée dans ce pilote : elle ne s'évalue que par son effet aval et viendra ensuite.

## 2. Coordonnées gelées

| Élément | Valeur | Source |
| --- | --- | --- |
| Machine | MacBook Pro M5 Pro, 64 Go, macOS 27.0.1 | relevé du 2026-10-05 |
| Serveur | LM Studio 0.4.25+1, moteur MLX 1.11.0 (nax) | relevé du 2026-10-05 |
| Candidat local de la comparaison | celui que désigne le tamis local (section 4) | règle fixée d'avance |
| Harnais réel | oh-my-pi `omp` 18.4.10 | test de fumée : outils structurés, tests réellement lancés, contexte le plus léger |
| Harnais neutre | mini-swe-agent (à installer, version à épingler) | isole la qualité du modèle |
| Référence cloud courante | Eiffel sur le profil routé actuel (Sonnet 5.5, effort medium) | routage Foundry |
| Cloud économique | Eiffel sur le palier economy (Haiku 4.5) | routage Foundry |
| Revue | Maigret (Opus 5.5, effort high), identique pour tous les bras | routage Foundry |

Empreintes des poids, révision, gabarit de conversation et paramètres de génération sont relevés au préflight et consignés. Un changement de l'un de ces éléments ouvre une version 2 ; les séries ne se mélangent pas.

## 3. Corpus

Des tickets déjà résolus de ce dépôt, rejoués à leur SHA de base (méthode de FOUNDRY-ADR-0019). Le dépôt compte 84 PR mergées au 2026-10-05, dont 26 de 300 lignes modifiées ou moins ; 13 satisfont tous les critères ci-dessous.

Critères d'inclusion, fixés avant de regarder les résultats :
- PR de 300 lignes modifiées ou moins, code et tests dans `plugins/foundry` ;
- critères d'acceptation vérifiables par des tests ;
- exclus : migrations de données, authentification ou autorité, concurrence sensible, gates et mécanisme d'évaluation, tickets purement documentaires.

Taille : 6 tâches pour la comparaison et 6 autres pour le tamis local, tirées et gelées avant le premier essai, plus une file de remplacement. L'énoncé remis au candidat est le titre et le corps du ticket tels qu'écrits dans le tracker, jamais la description de la PR ni le diff mergé.

Jugement mécanique : les tests ajoutés par la PR d'origine servent de tests protégés. Ils sont retirés du dépôt remis au candidat et appliqués ensuite par le juge. Un test écrit par le candidat ne compte jamais comme preuve. La ressemblance avec le diff d'origine n'est pas un critère.

## 4. Tamis local des candidats, sans cloud

Avant la comparaison, plusieurs candidats locaux tentent les 6 tâches du tamis, sans aucune invocation cloud : une tentative bornée par tâche (20 minutes et 40 étapes), sous le harnais réel, jugée par les tests protégés.

Candidats, sur un seul axe (ce n'est pas une matrice) :

- Qwen3.8-27B, MLX 6 bits, `lmstudio-community/Qwen3.8-27B-MLX-6bit` (déjà présent) ;
- Qwen3.8-27B, MLX 4 bits, `mlx-community/Qwen3.8-27B-4bit` (déjà présent) ;
- Qwen3.6-35B-A3B, MLX 4 bits, `lmstudio-community/Qwen3.6-35B-A3B-MLX-4bit` (déjà présent) ;
- au plus un candidat d'un autre éditeur, désigné par une note de sélection datée et sourcée, versée au dépôt avant le premier essai du tamis ; à défaut, le tamis se fait à trois.

Règle fixée d'avance : est retenu le candidat qui fait accepter le plus de tâches par le juge ; à égalité, celui dont la durée totale est la plus courte. Si aucun candidat n'atteint 2 tâches acceptées sur 6, la campagne s'arrête sur le verdict « conserver le cloud ». Le candidat retenu devient le candidat local de la comparaison, sur les 6 tâches de comparaison, qui n'ont pas servi au tamis.

Les mesures machine du tamis (vitesses, mémoire, swap) sont consignées pour chaque candidat. Coût : au plus 2 heures de machine par candidat, aucun quota cloud.

## 5. Parcours comparés

Chaque tâche est jouée dans les trois parcours, depuis la même base Git, avec les mêmes critères d'acceptation et la même revue.

- **A, cloud courant** : Eiffel produit le correctif, tests protégés, puis revue Maigret ; corrections cloud jusqu'à acceptation, dans la limite des escalades de Foundry.
- **B, cloud économique** : identique à A avec l'implémenteur du palier economy.
- **C, hybride** : une seule tentative locale bornée (20 minutes et 40 étapes), tests protégés ; en cas de succès, revue Maigret ; en cas d'échec ou de blocage de revue, reprise par le parcours A, dont le coût s'ajoute.

Le parcours local est aussi joué sous le harnais neutre, sans validation cloud : cela ne coûte que du temps machine et dit si un échec vient du modèle ou du harnais.

## 6. Mesures, par tâche et par parcours

- acceptation finale (tests protégés verts et revue sans blocage) ;
- tokens cloud premium par classe et par rôle, revues, corrections et reprises comprises, lus dans les journaux de session ;
- nombre de tours de revue, acceptation au premier passage ;
- durée murale jusqu'à l'acceptation ;
- pour le local : réussite de la tentative, étapes, vitesse de préremplissage et de génération, mémoire du serveur, swap avant et après, pression mémoire ;
- interventions humaines imprévues.

Une donnée absente reste inconnue. Aucun montant en dollars n'est présenté comme une économie.

## 7. Règle de décision, fixée d'avance

Le parcours hybride est **retenu pour cet usage** si, sur le corpus :
- qualité : toutes les tâches acceptées, et pas plus de tours de revue au total que le parcours A ;
- économie : travail premium total inférieur d'au moins 25 % à celui du parcours A ;
- temps : durée totale au plus 2 fois celle du parcours A ;
- machine : aucun essai interrompu pour mémoire, et swap supplémentaire sous 10 Go.

Sinon le cloud est conservé. Le parcours B est jugé avec les mêmes seuils : si B suffit, c'est lui la recommandation, pas le local.

Arrêt anticipé pendant la comparaison :
- moins de 2 tentatives locales réussies sur 6 : arrêt, verdict « conserver le cloud » ;
- travail premium cumulé de C supérieur ou égal à celui de A : arrêt.

Le corpus éligible compte 13 tickets : il ne permet pas d'étendre la comparaison à 12 tâches tenues à l'écart du tamis. Une extension demandera un nouveau tirage dans une version 2. Un échantillon de 6 tâches n'est pas une preuve statistique générale ; le rapport le dira.

## 8. Budgets et autorisations

- **Cloud** : la comparaison représente environ 12 exécutions d'implémenteur et 18 revues au minimum, davantage en cas de corrections. Plafond : 45 exécutions d'agent cloud. Le volume en tokens est inconnu avant mesure. Le tamis n'en consomme aucune.
- **Local** : au plus 2 heures de machine par candidat pour le tamis (8 heures à quatre candidats), puis environ 12 tentatives de 20 minutes au plus pour la comparaison, soit 4 heures.
- **Disque** : aucun téléchargement pour les trois candidats Qwen ; un téléchargement pour le quatrième candidat s'il est désigné, supprimable ensuite ; mini-swe-agent est un petit paquet Python.
- **Chargement du modèle** : confirmation du mainteneur avant chaque session de chargement.
- **Machine dédiée** : les mesures se font machine dédiée à la campagne, sans autre modèle chargé ni autre projet consommateur de mémoire ; le préflight relève le swap et la pression mémoire de départ et refuse de lancer si un autre modèle est chargé.
- **Isolement** : worktree jetable hors du checkout de développement, configuration de harnais isolée, écriture interdite hors du dossier d'essai, réseau du candidat limité à la boucle locale.

## 9. Ce qu'il faut construire avant de mesurer

Le comparateur d'ADR-0019 n'existe pas comme outil dans ce dépôt. Tickets à créer par intake :
1. Préparer le corpus : tirage, gel, extraction des tests protégés, bundles rejouables par tâche.
2. Construire le lanceur de comparaison : trois parcours, juge mécanique, relevé des tokens depuis les journaux de session, relevé machine.
3. Exécuter le tamis local puis la comparaison sur 6 tâches, et rendre les trois verdicts (compatibilité, qualité, économie).
4. Décider : nouveau tirage pour étendre, adoption limitée par une nouvelle ADR, ou conservation du cloud.

## 10. Constats déjà acquis (test de fumée du 2026-10-05)

- Les trois harnais (Codex, OpenCode, `omp`) font fonctionner le candidat avec des appels d'outils sur une micro-tâche ; cela ne mesure pas la qualité.
- Codex a annoncé des tests verts sans les avoir lancés : la vérification doit rester indépendante du modèle.
- LM Studio n'a pas appliqué la limite de contexte demandée (169 728 au lieu de 32 000).
- Le swap de la machine est passé d'environ 20 Go à 38,8 Go utilisés pendant l'essai, parce qu'un autre projet chargeait des modèles en parallèle ; ce n'est pas le cas nominal, d'où la condition de machine dédiée.
