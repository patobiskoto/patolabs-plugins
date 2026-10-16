> **ADR** · statut : `accepted`

## Contexte

La boucle principale représentait 58,9 % du coût du pilote v1. La mesure faite sur dix
sessions de développement réelles en explique le mécanisme : le contexte n'est pas payé
une fois, il est relu à chaque tour.

| profondeur | tokens relus / tour | $-équivalent / tour |
| -- | -- | -- |
| tours 1-10 | 52 k | 0,026 |
| tours 51-100 | 110 k | 0,055 |
| tours 201-400 | 242 k | 0,121 |
| tours 401+ | 400 k | 0,200 |

Un tour profond coûte 7,7 fois un tour initial. L'amorçage d'une session fraîche est de
26 k tokens de cache write, soit environ 0,26 $ au tarif 1h — donc redémarrer s'amortit
en deux à quatre tours dès qu'une session est profonde. Le coût d'une session suit
approximativement la somme, sur ses tours, de la taille du préfixe à ce tour : il croît
plus vite que le travail accompli.

Foundry possède déjà les deux pièces nécessaires pour s'en affranchir. `resume-issue`
recharge l'issue, l'état des AC, les contraintes ADR, les notes d'avancement, l'état git
et la PR **sans re-briefing humain** : l'état de vérité vit dans le tracker, dans git et
dans la PR, jamais dans l'historique de conversation. Et `command_runtime` sait déjà
lancer une session hôte headless bornée et en récupérer l'identité, la durée et le coût.
Ces capacités ne servent aujourd'hui qu'aux enfants de campagne.

Par ailleurs, le test humain n'est pas un gate appliqué : le skill `merge-pr` le
qualifie de `recommended`, et seulement pour une surface UI/runtime. Les seuls gates
mécaniques sont la CI prouvée verte (FOUNDRY-ADR-0002) et la preuve de review. Il n'y a
donc, en pratique, aucune validation humaine du fait qu'une solution répond au besoin.

## Décision

### Sessions jetables, état durable

Chaque étape du pipeline s'exécute dans une session hôte fraîche et bornée : sélection,
démarrage, implémentation, ouverture de PR, review, merge. Le handoff entre étapes passe
exclusivement par le tracker, git et la PR. Aucune étape ne dépend de l'historique de
conversation d'une étape précédente.

Une session est jetable par construction : l'interrompre à tout moment ne perd que du
travail non commité, jamais de l'état de décision.

### Le cadenceur est du code

L'enchaînement est piloté par un driver déterministe, sans contexte de modèle et sans
coût en tokens. Il est sans état entre deux ticks : il relit l'état depuis le tracker,
git et le code-host, en déduit l'unique prochaine étape, lance une session bornée pour
cette étape, enregistre son receipt, et recommence. Un driver tué et relancé reprend
sans intervention.

Les deux attentes asynchrones — CI en cours, validation humaine en attente — sont
traitées par relecture d'état, jamais en maintenant une session vivante. Maintenir une
session ouverte pendant une attente est explicitement interdit : elle repaierait son
préfixe entier à chaque tour sans produire de travail.

Le driver ne détient aucune autorité nouvelle. Il ne décide pas d'un verdict, ne
contourne aucune gate, ne modifie ni scope ni modèle. Il ordonnance des étapes qui
possèdent déjà leurs propres gates.

Il réutilise du sous-système de campagne ses seules primitives — receipts idempotents et
lancement borné — et **pas** son protocole distribué de leases, réservations et
réconciliation, conçu pour une concurrence multi-écrivains qui n'existe pas ici. Toute
reproduction de ces mécanismes sans nécessité démontrée doit être refusée en revue.

La limite de travail en cours est de un par défaut.

### Le verdict humain se place sur l'Epic

L'Epic porte le besoin, la façon de vérifier que la solution y répond, et la procédure
par laquelle un humain teste l'implémentation complète. Les issues portent la solution à
implémenter et les critères d'acceptation vérifiables techniquement.

La validation humaine s'exerce donc une fois, sur l'Epic, et non à chaque issue. C'est
un verdict de catégorie 1 au sens de FOUNDRY-ADR-0014 : établir qu'une solution répond à
un besoin est une décision de produit, pas un constat mécanique. Une issue prise isolément
n'est le plus souvent pas testable humainement ; le besoin, lui, l'est.

Quatre bornes rendent ce déplacement sûr :

1. **La preuve CI ne bouge pas.** FOUNDRY-ADR-0002 reste intégralement en vigueur, par
   PR et par merge. Seul le test humain se déplace ; aucune gate mécanique n'est
   assouplie, différée ou agrégée.
2. **Un Epic est un besoin qu'un humain peut valider en une seule séance.** Le cadrage
   doit produire une procédure de test exécutable, pas une intention, et refuser un Epic
   dont l'implémentation complète ne peut pas être éprouvée d'un bloc.
3. **Tout travail de code appartient à un Epic.** Une issue sans Epic parent conserve sa
   validation humaine propre ; elle ne peut pas devenir un chemin d'échappement.
4. **Un échec de validation ouvre des issues de remédiation sous le même Epic**, jamais
   un revert. La branche par défaut n'est pas la production : FOUNDRY-ADR-0011 sépare
   déjà la preuve de livraison de la possession du déploiement.

La clôture d'Epic devient le porteur de ce verdict et doit enregistrer un receipt de
validation humaine explicite, horodaté et lié à l'ensemble exact des enfants terminaux.

## Conséquences

Le coût de la boucle principale cesse de croître avec la durée d'une issue. La discipline
d'écriture devient obligatoire : ce qu'une session sait doit être écrit dans le tracker,
sinon la suivante l'ignore. C'est le comportement que Foundry prétend déjà imposer et que
la mémoire conversationnelle rendait optionnel.

L'unité de mesure du programme de coût devient le **$-équivalent par Epic livré et validé**
**humainement**, remédiations incluses. C'est un meilleur dénominateur que l'issue mergée :
il porte un signal de qualité humain, sans lequel l'optimum d'une métrique de coût est
trivialement « prendre le modèle le plus puissant ».

Le TTL de cache doit passer de 1 h à 5 min pour les sessions de chaîne : l'écriture 1 h
coûte 1,6 fois l'écriture 5 min, prime justifiée pour une session longue, gaspillée pour
une session courte abandonnée.

Le risque principal se déplace vers le cadrage : un seul gate humain qui compte au lieu
de plusieurs gates mous. Un Epic mal cadré produit une découverte tardive d'exigences non
écrites, à l'échelle de tous ses enfants.

L'identité OAuth de l'hôte Claude Code est liée à la machine (FOUNDRY-101). Le driver
s'exécute donc sur le poste de développement, pas sur un serveur distant.

## Alternatives écartées

* **Garder une session longue par issue** — c'est l'état actuel ; son coût croît plus
  vite que le travail et la mesure le chiffre à 7,7x sur les tours profonds.
* **Faire porter l'enchaînement par un modèle économique** — complémentaire mais plus
  faible : un modèle moins cher agit linéairement sur le prix du token, le découpage agit
  multiplicativement sur leur nombre. Et l'ordonnancement doit être déterministe.
* **Faire s'auto-enchaîner les sessions** — une session qui lance sa suivante rompt la
  chaîne si elle échoue et n'offre aucun point d'observation stable.
* **Réutiliser le coordinateur de campagne tel quel** — importerait un protocole
  distribué conçu pour une concurrence absente, celui-là même qui a produit une vingtaine
  de correctifs consécutifs.
* **Merge autonome sur une classe de risque déclarée** — l'issue la plus coûteuse du
  pilote était précisément un `chore` à portée sécurité mal cadrée ; une classification
  de risque auto-déclarée n'aurait pas capté le cas.
* **Conserver le test humain par issue** — il n'est pas appliqué aujourd'hui, et il porte
  sur un objet qui n'est généralement pas testable seul.