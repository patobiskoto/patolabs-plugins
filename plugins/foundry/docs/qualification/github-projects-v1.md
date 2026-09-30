# PAT-65 — qualification GitHub Projects v2 et stockage ADR

Date de sondage : 27 septembre 2026. Ce rapport borne des observations sur les
ressources personnelles GitHub explicitement autorisées. Il ne modifie pas le binding
du dépôt : `patolabs-plugins` reste sur Linear. Les sondes n’ont produit aucun adaptateur, receipt Foundry ni attestation de
lifecycle. Leurs résultats restent distincts des mutations tracker et du lifecycle
Foundry pilotés ensuite par le coordinateur.

## Ressources et identité relues avant les effets

| Ressource | Coordonnées relues | Résultat |
| --- | --- | --- |
| Compte | `patobiskoto` (id `1763337`) | identité du jeton confirmée |
| Dépôt de sable | [`patobiskoto/foundry-v1-ghprojects-sandbox`](https://github.com/patobiskoto/foundry-v1-ghprojects-sandbox) (id `1390303610`, node `R_kgDOUt5Zeg`) | créé privé ; conservé |
| Projet personnel | [Foundry V1 — qualification GitHub Projects](https://github.com/users/patobiskoto/projects/7) (nº 7, `PVT_kwHOABroCc4Bk0U-`) | créé privé, ouvert ; aucune organisation sondée |
| Issues synthétiques | [parent #1](https://github.com/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1), [enfant #2](https://github.com/patobiskoto/foundry-v1-ghprojects-sandbox/issues/2) | créées, conservées |
| Support ADR expérimental | [issue #3](https://github.com/patobiskoto/foundry-v1-ghprojects-sandbox/issues/3), [archive v0](https://github.com/patobiskoto/foundry-v1-ghprojects-sandbox/issues/3#issuecomment-5853484842), [archive v1](https://github.com/patobiskoto/foundry-v1-ghprojects-sandbox/issues/3#issuecomment-5853489272) | créé, lu, évolué et relu ; support désormais marqué `foundry:adr` |

Le jeton classique présentait `project` et `repo` dans `X-OAuth-Scopes`. Cela prouve
les effets ci-dessous avec ce jeton sur ce sable, pas un minimum universel ni les
permissions d'un jeton fin-grained ou d'une GitHub App. La documentation officielle
indique `read:project` pour les lectures et `project` pour requêtes et mutations
Projects ; la permission `Contents` est en plus demandée à une GitHub App quand elle
lie un projet à un dépôt. [GitHub Docs — API Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects)

## Résultats exercés

| Parcours | Preuve observée | Portée et limite |
| --- | --- | --- |
| Projet et champs | Le projet a les champs GitHub natifs, dont `Status`, `Parent issue` et `Sub-issues progress`. La création du champ single-select `Foundry priority` (`P1`, `P2`) a réussi ; son option `P1` et `Status=In Progress` ont été affectés à l'item de #2 et relus. | La première création a été refusée clairement par validation : chaque option exigeait `description`. Après relecture prouvant l'absence d'effet, une seule reprise corrigée a réussi. Les mutations GraphQL sont qualifiées sur ce projet personnel, jamais sur une organisation. |
| Item ↔ issue | #1 et #2 ont été ajoutées via `addProjectV2ItemById`, avec items `PVTI_lAHOABroCc4Bk0U-zg8_3r4` et `PVTI_lAHOABroCc4Bk0U-zg8_3sM`, puis relus. | L'API documente qu'un ajout dupliqué renvoie l'item existant ; ce comportement n'a pas été provoqué, afin de ne pas présenter un résultat non sondé comme une clé de rejeu. [Documentation](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects) |
| Parent/sous-issue | `POST /issues/1/sub_issues` avec l'id de #2 a réussi ; `GET /issues/2/parent` relit #1. | C'est une relation native d'Issues REST, distincte de l'item Projects. Elle exige l'écriture Issues pour un jeton fin-grained. [Documentation](https://docs.github.com/en/rest/issues/sub-issues) |
| Dépendance | #2 a été lié à #1 par `POST /issues/2/dependencies/blocked_by`, puis relu par la liste. | Les dépendances sont aussi des Issues REST, avec permission Issues write documentée pour un jeton fin-grained. [Documentation](https://docs.github.com/en/rest/issues/issue-dependencies) |
| Pagination | `ProjectV2.items(first:1)` a retourné #1 avec `hasNextPage=true`; la page `after=endCursor` a retourné #2 avec `hasNextPage=false`. Les issues REST avec `per_page=1` ont aussi retourné deux pages et le lien `rel=next`. | Pagination réellement exercée sur deux items. Les issues REST documentent `per_page` maximum 100. Aucun plafond de volume Projects, filtrage de backlog ou projection organisationnelle n'est qualifié. |
| REST Projects | `GET /users/patobiskoto/projectsV2/7` a retourné 200, projet privé, node id identique et API version `2022-11-28`. | Cela corrige l'hypothèse trop large « Projects v2 GraphQL-only » : lecture REST actuelle observée. Les mutations Projects réellement sondées ici sont GraphQL ; aucune mutation REST Projects n'est affirmée. La règle REST du code-host PR demeure séparée. [REST Projects](https://docs.github.com/en/rest/projects/projects) |

Les snapshots sans secret sont sous `/tmp/pat65-*` pendant cette session, notamment
`pat65-project-fields-initial.json`, `pat65-items-page1.json`,
`pat65-items-page2.json`, `pat65-rest-project-v2-get.txt`,
`pat65-adr-s1-before-patch-v1.json`, `pat65-adr-s3-v1-readback.json` et
`pat65-resume-comments.json`. Les commandes ont utilisé `gh api` direct : REST pour
repo/issues/relations et GraphQL pour Projects. Toutes les cibles et l'identité ont été
relues avant chaque effet ; aucune suppression ni archivage n'a été appelée.

## Complément : vocabulaires et estimation, qualification seulement

Le même projet personnel privé porte maintenant trois champs expérimentaux, créés
seulement après relecture qu'ils étaient absents :

| Champ de qualification | IDs et valeurs créées | Effet et relecture |
| --- | --- | --- |
| `Foundry normalized state` | `PVTSSF_lAHOABroCc4Bk0U-zhjjjyI` ; `backlog`, `ready`, `in-progress`, `review`, `blocked`, `done`, `dropped` | Chaque option a été affectée à l'item de #2 et relue immédiatement. La dernière valeur conservée est `dropped`. |
| `Foundry type` | `PVTSSF_lAHOABroCc4Bk0U-zhjjj4s` ; `Bug`, `Feature`, `Task`, `Epic` | Chaque option a été affectée à l'item de #2 et relue immédiatement. Après l'acceptation de `PAT-ADR-0007`, #1 et #2 ont été fixées à `Task` et relues; #3 est discriminée par le label réservé décrit ci-dessous. |
| `Foundry estimate` | `PVTF_lAHOABroCc4Bk0U-zhjjj4w` ; type GraphQL `NUMBER` | `3` a été écrit puis relu comme `3.0`. `clearProjectV2ItemFieldValue` a réussi et la relecture donne `null`. |

Chaque mutation a précédé une lecture de `viewer=patobiskoto`, du projet exact
`PVT_kwHOABroCc4Bk0U-`, de son titre et de son caractère privé. Les sorties et
relectures sont dans `/tmp/pat65-create-normalized-state-field.json`,
`pat65-create-type-field.json`, `pat65-create-estimate-field.json`,
`pat65-state-read-{backlog,ready,in-progress,review,blocked,done,dropped}.json`,
`pat65-type-read-{Bug,Feature,Task,Epic}.json`,
`pat65-after-estimate-set.json` et `pat65-after-estimate-clear.json`.

La relecture après le clear confirme que la mutation ciblée de l'estimation a préservé
les valeurs non visées à cet instant : `Status=In Progress`, `Foundry priority=P1`, labels vides,
état normalisé `dropped` et type `Epic`. La qualification de typage postérieure a
ensuite modifié seulement ce dernier champ de #1/#2 vers `Task`. C'est une observation de propriétés distinctes
dans un seul item personnel, pas une preuve d'absence de course ni une autorisation de
mapping durable. `Labels`, `Milestone`, `Assignees` et `Repository` sont des propriétés
de l'Issue/PR et non des valeurs d'item modifiables par
`updateProjectV2ItemFieldValue`. [Documentation](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects)

La documentation GitHub indique jusqu'à 50 options pour un champ single-select
Projects. Les 7 et 4 options ci-dessus sont donc sous ce plafond, sans l'éprouver.
Le plafond de champs et les limites de nom/taille applicables au projet personnel
exactement sondé ne sont pas qualifiés ici : aucune saturation n'a été provoquée et les
limites propres aux issue fields organisationnels ne sont pas généralisées.
[Documentation sur les single-select](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-single-select-fields)

Ces deux vocabulaires et l'estimation sont des objets de qualification uniquement. Ils
ne modifient pas le binding actif Linear. Leur forme ne remplace pas la décision
acceptée dans `PAT-ADR-0007` ni le contrat des parcours cœur.

## Complément après acceptation de PAT-ADR-0007 : type ADR et exclusion Kanban

`PAT-ADR-0007` est maintenant **accepted**. Elle impose qu'une issue support ADR porte
un type explicite, filtrable, et que le Kanban exclue ces supports sans les masquer des
liens documentaires. La sonde a donc cherché une capacité native avant de choisir la
représentation : les lectures GraphQL du projet personnel ne présentent que les
champs Projects et les contenus `Issue`; aucun type natif d'issue n'a été présumé ni
configuré.

La représentation qualifiée est le label de dépôt réservé **`foundry:adr`** (couleur
`5319e7`, description « Reserved Foundry ADR support marker; exclude from delivery
Kanban. »). Il est appliqué à l'issue support #3 et relu. Le champ Project existant
`Foundry type` reste utile pour les items de livraison : #1 et #2 ont chacun été mis à
`Task` puis relus; #3 n'a aucune valeur dans ce champ et son seul marqueur sémantique
est `foundry:adr`. Ainsi, l'absence ou l'ambiguïté du label réservé ne peut pas être
assimilée silencieusement à une tâche par un futur adaptateur.

La discrimination et le filtre ont été exercés sur le projet personnel privé exact
`PVT_kwHOABroCc4Bk0U-` : la lecture GraphQL finale retourne trois items, #1 et #2 de
type `Task`, puis #3 portant `foundry:adr`. Le prédicat Kanban exécuté
`items dont labels ne contient pas foundry:adr` retourne exactement #1 et #2; la
requête REST `GET /repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues?state=all&labels=foundry:adr`
retourne exactement #3. GitHub documente que les vues Projects acceptent le filtre
`label:LABEL` et sa négation; le filtre concret à configurer pour un Kanban de
livraison est donc **`-label:foundry:adr`**.
[Documentation GitHub sur les filtres Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/filtering-projects)
et [paramètre REST `labels`](https://docs.github.com/en/rest/issues/issues#list-repository-issues).

La configuration est aussi qualifiée par API GraphQL sur ce projet personnel : une vue
nommée `PAT-65 qualification — delivery Kanban` a été créée avec
`layout=BOARD_LAYOUT` (`PVTV_lAHOABroCc4Bk0U-zgL0QXI`), puis sa seule propriété
visée `filter` a reçu `-label:foundry:adr`; les deux réponses et la relecture de la
connexion `views` confirment le même ID, nom, layout et filtre. C'est la preuve de
l'exclusion configurée dans un Kanban GitHub réel. Aucun navigateur connecté n'était
disponible, donc l'affichage visuel des colonnes et cartes n'a pas été inspecté. La
lecture REST de `GET /users/patobiskoto/projectsV2/7/views`, y compris avec la version
API `2026-03-10`, a répondu `404`; elle ne généralise pas l'absence de support,
puisque la capacité GraphQL a été exercée avec succès.

Les écritures de labels ont utilisé uniquement l'endpoint d'ajout ciblé
`POST /issues/3/labels`; aucun endpoint de retrait ou de remplacement global n'a été
appelé. Après avoir posé `foundry:adr`, la sonde a ajouté le contrôle non sémantique
`pat65:preserve`; la relecture conserve les deux labels et le corps/titre de #3 est
identique. Le SHA-256 du corps relu est toujours
`3e24cdf4e2d5dbc15cf71ba69c2a399ed5ae99dcc8d4490d56e2f5cda10e4dbc`.
Il est calculé sur la valeur JSON exacte `body`, encodée en UTF-8, sans ajouter de
terminaison de ligne (`sha256(json.loads(...)["body"].encode("utf-8"))`). Le digest
précédemment rapporté provenait d'un outil qui avait haché ce corps avec un LF final
supplémentaire ; il ne décrit donc pas les octets relus par l'API.
Ce contrôle prouve la préservation d'un label préexistant pour cette API, pas une
garantie contre une écriture concurrente.

Le jeton de sonde est classique avec `repo` et `project`: il prouve ces effets sur ce
sable privé, pas le jeu minimal de permissions. GitHub documente `Issues: read` pour
la lecture REST par labels; les permissions minimales pour créer le label, l'ajouter
à une issue privée, ajouter un item Projects et écrire son champ n'ont pas été
isolées avec un PAT fin-grained ni une GitHub App. Elles restent non qualifiées.
Le projet est personnel et privé; organisations, dépôts publics, types d'issue natifs,
GitHub App et rendu visuel des colonnes/cartes restent hors de cette preuve.

Les preuves non secrètes de ce complément restent locales à cette session :
`/tmp/pat65-typing-final-kanban-source.json`,
`/tmp/pat65-typing-final-kanban-filter-result.json`,
`/tmp/pat65-typing-final-adr-label-rest-filter-result.json`,
`/tmp/pat65-typing-preserve-label-add-result.json`,
`/tmp/pat65-typing-final-issue3-body.sha256` et
`/tmp/pat65-typing-view-create-readback.json`,
`/tmp/pat65-typing-view-filter-readback.json` et
`/tmp/pat65-typing-view-list-20260310.txt`. Elles ne sont pas des receipts Foundry.

## ADR expérimental : résultat, intégrité et reprise

L'issue #3 a reçu une source UTF-8 comprenant listes `-` et `*` distinctes, lignes
vides, chevrons, esperluette, bloc de code, indentation, emphase multi-ligne et lien.
La lecture `application/vnd.github.raw+json` via l'API a donné exactement le SHA-256
v0 `581127c609ad230c7ea9c9864c53c807a49f1746c5610d1d9381fc1f5b50b4a1`.

Avant son évolution, le commentaire v0 a enregistré ce digest et la source lisible.
Après une S1 fraîche qui a revérifié v0, une seule mise à jour a produit la source v1
(`status: accepted` et une ligne d'évolution) ; la S3 a retrouvé les octets v1. Le
commentaire v1 relie v1 à ce digest v0. Une reprise expérimentale a relu l'issue et les
commentaires et a trouvé exactement une archive v0 et une archive v1. C'est une
convergence par lecture de ce jeu de test, pas une idempotence fournie par GitHub : les
IDs d'issues et de commentaires sont alloués par le serveur et ne sont pas
déterministes ; `clientMutationId`, lorsqu'une mutation le propose, sert seulement à
la corrélation.

Cette expérience ne prouve ni CAS ni immutabilité. Une modification externe entre S1
et S2 peut encore être écrasée puis masquée par S3 ; une réponse ambiguë impose une
lecture et une résolution humaine en cas de zéro ou plusieurs candidats. Les commentaires
GitHub restent modifiables et leur historique peut avoir son contenu supprimé par un
auteur ou un contributeur en écriture. Les archives v0/v1 sont donc un profil
d'intégrité expérimental et un historique lisible, pas une attestation cryptographique
du fournisseur ni un receipt Foundry. [Historique des commentaires](https://docs.github.com/en/communities/moderating-comments-and-conversations/tracking-changes-in-a-comment)

Les Discussions sont une alternative possible mais non qualifiée : l'API GraphQL les
expose comme objets modifiables et paginés ; elles n'ont pas été créées dans ce sable.
Elles ne doivent donc pas devenir un fallback implicite. [Schéma Discussions](https://docs.github.com/en/graphql/guides/using-the-graphql-api-for-discussions)

## Décision acceptée : PAT-ADR-0007

`PAT-ADR-0007` accepte **une issue GitHub par ADR dans le dépôt canonique, corps courant lisible, et
commentaires de versions append-only par convention**. Chaque commentaire de version doit porter le
digest du corps source UTF-8, le digest prédécesseur, la source lisible et les
coordonnées canoniques. La lecture vérifierait toute la chaîne et refuserait les
doublons, trous, digests divergents, modification du corps courant ou réponse ambiguë.
La reprise chercherait le candidat exact et s'arrêterait sur ambiguïté ; elle ne
rejouerait jamais aveuglément une création de commentaire.

Les issues partagent la visibilité du dépôt : GitHub ne fournit pas ici d’issue
privée dans un dépôt public. La portée initiale décidée est donc limitée aux dépôts
privés et projets personnels effectivement qualifiés. Un dépôt public ou une autre
forme de propriété reste non qualifié, sans publication automatique d’ADR privées.

La décision adopte la garantie bornée S1/S2/S3/S4/S5 de `PAT-ADR-0006`, nomme la
fenêtre résiduelle S1→S2 et déclare le caractère modifiable/supprimable de
l'historique GitHub. Elle ne doit pas être présentée comme l'équivalent de l'intégrité
byte-exacte avec témoins du provider Linear (`PAT-ADR-0002`). Discussions restent non
qualifiées et ne sont pas un fallback.

## Forecast concret proposé au mainteneur

| Ticket | Tranche proposée | Dépendance et sortie vérifiable |
| --- | --- | --- |
| PAT-57 | 57-A : modèle de lecture Issue + Project item, curseurs, champs natifs et erreurs/redaction ; 57-B : recherche/filtrage, exclusion explicite `foundry:adr` et limites de pagination | La représentation décidée est issue/item, `Foundry type=Task` pour le travail et label réservé `foundry:adr` pour le support. Tests avec pages, données redacted/absentes et support absent/ambigu refusé. Pas de promesse organisationnelle. |
| PAT-58 | 58-A : décision de support et garantie **acceptée** dans `PAT-ADR-0007`; 58-B : codec strict source/digest/chaîne et lecture ; 58-C : création, évolution et reprise ambiguë | Le gate de décision est levé seulement pour le périmètre privé/personnel décidé. 58-B/C doivent tester les digests, le Markdown varié, zéro/un/plusieurs candidats, le label ADR absent/ambigu et une divergence externe. |
| PAT-66 | 66-A : création/commentaire/relations Issues ; 66-B : mises à jour sous la garantie retenue | Les liens ADR dépendent désormais des tranches 58-B/C; les mutations d'issue doivent elles aussi appliquer la garantie sans CAS décidée. |

PAT-65 est qualifié dans sa portée personnelle privée : les sondes, la décision
`PAT-ADR-0007` acceptée, le type ADR filtrable et l'exclusion de données Kanban sont
documentés. Le forecast est mis à jour ici sans modifier les critères d'acceptation de
PAT-57 ou PAT-58. Les limites restantes sont explicites : permissions minimales,
organisation, dépôt public, type d'issue natif, rendu visuel des colonnes/cartes et
GitHub App.


## Complément PAT-66 : relation native `relatesTo`

Le 27 septembre 2026, l'introspection du schéma GitHub authentifié expose
`addRelatesTo`, `removeRelatesTo` et la connexion paginée `Issue.relatesTo`.
La qualification PAT-65 ne couvrait pas cette relation ; son absence de ce rapport
initial ne signifie donc pas que le fournisseur la refuse. La matrice V1 exige
`link(relates)` comme opération cœur, distincte des dépendances directionnelles.

La sonde PAT-66 utilise le binding actif `GHQUAL`, le même dépôt privé personnel
`patobiskoto/foundry-v1-ghprojects-sandbox` et le Project 7
`PVT_kwHOABroCc4Bk0U-`. Après vérification de ces coordonnées, du propriétaire
`User`, des deux issues canoniques #1/#2 et d'une seconde lecture identique des
relations attendues, un seul `addRelatesTo` a reçu leurs **node IDs d'Issue** dans
`issueId` et `relatedIssueId`. Les IDs REST numériques, numéros d'issue et IDs
Projects/items ne sont pas des substituts à ces coordonnées.

La réponse identifie les deux issues exactes. La relecture de `relatesTo` sur
chacune montre l'autre issue : la relation observée est symétrique. Les titres,
corps et labels des deux issues sont identiques aux snapshots précédents. Seule
cette relation a été ajoutée ; le binding principal reste Linear. Ces observations
ne prouvent ni CAS, ni exclusion des écritures concurrentes, ni exactly-once.
La fenêtre résiduelle S1→S2 de PAT-ADR-0006 reste assumée.

Sources locales de qualification : `/tmp/pat66-relates-schema-current.json`,
`/tmp/pat66-relates-mutation-schema.json`, `/tmp/pat66-relates-issue-schema.json`,
`/tmp/pat66-relates-preflight.json`, `/tmp/pat66-relates-add-result.json` et
`/tmp/pat66-relates-add-readback.json`. Ce sont des réponses API et des captures
de test, pas des reçus ou attestations Foundry. Les sondes ne qualifient pas les
permissions minimales, un dépôt public, une organisation ou une GitHub App.

## Recette PAT-66 : écritures par le port commun

La recette autorisée a été exécutée avec l’adaptateur source, via le binding
GHQUAL du dépôt privé et du Project personnel déjà qualifiés. Les issues
`GHQUAL-4`, `GHQUAL-5` et `GHQUAL-6` ont été créées une seule fois avec leurs
items, types, priorités, estimations et états explicites. L’enfant 5 a d’abord
été attaché au parent 4, puis déplacé au parent 6.

La priorité de 5 et son corps UTF-8 ont été modifiés séparément ; une relecture
a vérifié la conservation des autres propriétés. Un commentaire a été retrouvé
par son ID exact et son corps. La dépendance 5 → 4 et la relation native
symétrique 5 ↔ 4 ont été relues ; le rejeu de `relates` a observé le lien existant.
Le support ADR expérimental 3 et le binding Linear de patolabs-plugins restent
préservés. Ces observations ne sont pas des receipts d’acceptation Foundry.

Les limites restent celles de PAT-ADR-0006 : détection bornée, fenêtre S1→S2
résiduelle assumée, aucune transaction globale, CAS, exclusion concurrente ou
garantie exactly-once. Une création partiellement réalisée n’est pas relancée
à l’aveugle. Le cycle de vie PR/merge et le codec ADR restent hors PAT-66.

### Recette de reprise PAT-66 après interruption

Une création synthétique supplémentaire, `GHQUAL-7`, a été effectuée par le
port commun. Le transport de qualification a volontairement rendu sa réponse
indisponible et masqué temporairement le candidat à la relecture : le premier
processus a effectué un seul POST Issue puis refusé l’effet inconnu, en conservant
son intention locale. Cette perte de réponse est une injection de qualification,
pas une panne GitHub présentée comme réelle.

Un autre processus a retrouvé cette même Issue et ajouté son item. Sa relecture
immédiate n’a pas confirmé l’ajout ; l’adaptateur a signalé le résultat partiel
avec les IDs connus. Après observation fraîche de l’item unique, la reprise a
terminé les champs et le parent sans aucun second POST Issue. Un troisième
processus a relu le résultat complet sans aucune mutation fournisseur.

Le journal privé est une observation de reprise locale, pas un receipt ni une
preuve d’acceptation. Il coordonne les deux hôtes et worktrees qui partagent
le même répertoire de données Foundry sur une machine ; aucune coordination
entre machines ni garantie exactly-once globale n’est revendiquée. Zéro candidat
pour un effet encore inconnu reste un refus ; une création externe identique
dans la fenêtre initiale zéro → POST reste un risque résiduel explicite.

### Recette PAT-66 : champs et exclusion des supports ADR

Une nouvelle invocation a demandé la priorité et l’estimation déjà présentes
sur `GHQUAL-5` : aucune mutation de champ n’a été envoyée. La priorité a ensuite
été réellement modifiée de P1 vers P2 ; le transport de qualification a injecté
une perte de réponse après cette unique mutation. La relecture autoritative a
confirmé P2 et la conservation des propriétés non visées. Un nouveau rejeu vers
P2 n’a envoyé aucune mutation. Cette perte de réponse est une injection explicite,
pas une panne GitHub revendiquée.

Une tentative de commentaire de livraison sur le support ADR expérimental
`GHQUAL-3` a été refusée après les lectures de qualification et avant tout POST.
Le support et ses versions sont restés intacts. Les tests de transport couvrent
également une issue du dépôt absente du Project, le résultat partiel de plusieurs
champs, l’effet non observé et une divergence de propriété non visée.

Chaque champ possède une borne de lecture/écriture/relecture indépendante ;
aucune transaction entre champs ni exclusion concurrente n’est revendiquée.
Contrairement à la création, les champs existants n’ont pas de journal durable
d’intention : une relecture indisponible laisse un effet inconnu et aucun retry
automatique. Une invocation ultérieure explicite repart d’une lecture fraîche.
La fenêtre S1→S2 résiduelle de PAT-ADR-0006 reste assumée.

### Recette PAT-58 : cycle ADR natif et source exacte

La recette privée du 27 septembre 2026 utilise le port `Tracker` et les opérations
communes `write`, avec le binding sandbox `GHQUAL`. Trois supports synthétiques
non décisionnels ont été créés : `GHQUAL-ADR-0001` (issue 8),
`GHQUAL-ADR-0002` (issue 9), `GHQUAL-ADR-0003` (issue 10). Le cycle observé est :

- issue 8 : proposed → accepted, édition versionnée UTF-8 exacte, lien vers
  `GHQUAL-5`, puis superseded par l'ADR de l'issue 9 ;
- issue 9 : proposed → accepted, avec relation réciproque `supersedes` ;
- issue 10 : proposed → accepted → deprecated.

Le port relit la source, le statut et la chaîne complète des versions. Le GET
natif GitHub au format `application/vnd.github.full+json` fournit la source
`body` exacte et le rendu `body_html` : titre, emphase, code inline, tableau,
lien et code clôturé sont effectivement rendus. Les caractères `é`, `œ`, `漢字`
et les chevrons littéraux du code restent présents. Il n'y a ni réécriture de
source pour compenser l'affichage, ni qualification générale de tout Markdown
possible à partir de cette seule recette.

Lors de chaque création, l'ajout au Project a été momentanément invisible dans
la lecture suivante. Foundry a explicitement refusé `adr:item_effect_unknown`.
Une nouvelle invocation n'a repris que le candidat natif exact et unique déjà
observé, au moyen du journal d'intention conservé : aucune seconde issue n'a
été créée. Il s'agit d'un retard de visibilité observé, pas d'une panne GitHub
ni d'une perte de réponse artificiellement présentée comme réelle. Les tests
de transport couvrent séparément les pertes de réponse, zéro/un/deux candidats,
les préconditions périmées et les altérations.

Les trois supports sont absents du backlog de livraison renvoyé par le port.
La relecture native de la vue `PAT-65 qualification — delivery Kanban` confirme
`BOARD_LAYOUT` et le filtre `-label:foundry:adr` ; elle qualifie la configuration,
pas une inspection visuelle des cartes. L'issue expérimentale 3 conserve
exactement son titre, son corps, ses labels et les IDs/corps de ses commentaires.
Le marqueur tracker Linear de patolabs-plugins reste byte-identique.

Ces observations de recette ne sont pas des receipts Foundry. La sérialisation
d'intention est locale à une machine ; la supersession utilise deux commentaires
et n'est pas atomique. Le risque résiduel S1→S2 est assumé conformément à
PAT-ADR-0006, sans CAS ni exclusion des écritures concurrentes. Les propriétés
non concernées sont conservées lorsque l'API permet des écritures ciblées.

### Qualification de l'engagement de tête après la review PAT-58

Le 30 septembre 2026, la review indépendante a relevé qu'une suppression du
dernier commentaire laissait une chaîne préfixe valide. Avant correction native,
les trois supports synthétiques 8, 9 et 10 ont été relus contre les snapshots
conservés : mêmes IDs et corps de chaque commentaire, séquences contiguës,
digests prédécesseurs exacts, titres et sources courantes inchangés. Le binding
privé `GHQUAL` a été vérifié. Une écriture ciblée du seul titre de chaque Issue a
ensuite engagé sa séquence, l'ID du dernier commentaire et son digest complet.
La relecture du corpus a retrouvé les trois statuts attendus : `superseded`,
`accepted`, `deprecated`. Le titre et le corps de l'ancien support expérimental
3 sont restés inchangés.

Un quatrième support synthétique, `GHQUAL-ADR-0004` (issue 11), a exercé la
création puis l'acceptation avec ce format. Comme dans la première recette,
l'ajout de l'item au Project a d'abord été momentanément invisible : l'opération
a refusé `adr:item_effect_unknown`. Une lecture ultérieure a retrouvé l'unique
Issue 11 et son item, puis la reprise de la même intention a terminé la version
initiale sans créer d'autre Issue. Le passage à `accepted` a ajouté un seul
commentaire et avancé l'engagement de tête ; le port a relu deux commentaires,
le titre engagé et le statut `accepted`. Les tests de transport vérifient la
détection d'une tête supprimée ou modifiée et la reprise d'un commentaire dont
la mise à jour de titre a été interrompue. Ces essais n'attestent pas une
résistance à une modification coordonnée des commentaires et du titre.

La correction de la review suivante a qualifié la corroboration entre l'inventaire
exhaustif des Issues du dépôt et les items du Project. Sur le support synthétique
`GHQUAL-ADR-0004`, le retrait ciblé du label `foundry:adr` a fait refuser la
lecture du corpus avec `reserved support label missing`. Le label a été rétabli
une seule fois ; le titre engagé, le corps et le statut `accepted` ont été relus
inchangés. Le retrait d'un item Project, les réponses 403/404 avec empreinte
restante et les faux titres sans type ADR sont couverts par les tests de transport,
pas par une mutation native de cette recette.

Un cinquième support synthétique, `GHQUAL-ADR-0012` (issue 12), a qualifié la
nouvelle allocation : l'ID reprend le numéro natif GitHub, sans réutiliser un ID
après disparition complète d'un support. L'ajout au Project a encore été
momentanément invisible ; après observation de l'unique Issue 12 et de son item,
la même intention a repris sans second POST Issue. Un nouveau processus a rejoué
la création terminée : même ID, même commentaire initial, même source et même
engagement de tête. La suppression totale sans Issue ni item attribuable demeure
indétectable à la lecture ; l'allocation prévient seulement la réutilisation de
son ID. Les anciens couples `GHQUAL-ADR-0001`/issue 8 à
`GHQUAL-ADR-0004`/issue 11 restent lisibles.

## Qualification PAT-69 : clôture Epic bornée

Le 30 septembre 2026, l'identité et le périmètre ont été relus avant tout effet :
compte `patobiskoto` (id `1763337`), Project personnel privé nº 7
`PVT_kwHOABroCc4Bk0U-`, dépôt canonique privé
`patobiskoto/foundry-v1-ghprojects-sandbox` (id `1390303610`, node
`R_kgDOUt5Zeg`) et catalogue normalisé complet des champs State, Type, Estimate
et Priority. Le binding `GHQUAL` désigne exactement ces coordonnées.

La création commune a produit une seule Issue parent, `GHQUAL-15` (issue native
`5644346844`, node `I_kwDOUt5Zes8AAAABUG3l3A`), puis a refusé
`partial_create:item_readback` pendant le délai de visibilité de son item
`PVTI_lAHOABroCc4Bk0U-zg9okr0`. La reprise explicite de la même intention
`8175e1f937a1b8b99b7e30029664693a3f82642940c1b64b44763b9b357343b5`
a retrouvé l'unique Issue et l'unique item, puis a projeté Ready, Epic, P2 et
Estimate 1 sans second POST Issue.

Le lien natif parent-enfant vers `GHQUAL-13` a été appliqué une fois. La première
relecture locale l'a refusé parce que la version opaque de l'Epic, dérivée de son
snapshot complet, avait changé avec la relation attendue. Aucun second POST de
relation n'a été envoyé. La correction exclut seulement cette version dérivée de
la comparaison des propriétés non visées ; elle conserve la vérification native
réciproque et compare toujours titre, corps, labels, autres relations et champs
Project. Les tests provoquent séparément une dérive de chacune de ces propriétés
et vérifient son refus.

Avant clôture, la relecture complète a observé `GHQUAL-15` Ready, Epic, AC 0/1,
sans PR, version opaque `870381632526250840`, avec pour unique enfant requis
`GHQUAL-13`. Celui-ci était Done, AC 2/2, version
`704908415487686180`, et portait la preuve d'acceptation authentifiée
`53d56e4d367f3e72b91026b0352a607e68c4ef6afdc65018e45f30721813d6db`.
Le graphe de dépendances était vide et le digest de validation parent était
`f1b22c38d325c02949de6d0b2d1e62f9812b88f64589b0460decec7b936157e8`.

L'appel commun `close_epic` avec verdict humain `accepted` a ajouté l'unique
commentaire d'audit `5908584673`, projeté une seule fois `State=done`, puis relu
le graphe complet. Son reçu déterministe est
`github:epic:758ff6bb0f7ecd04c4edea8f743360fa65d818662a39a051048ce7f484dfe23a`
et la version parent fermée est `870381632526250841`. Titre, corps, labels,
relation enfant, Type Epic, Priority P2 et Estimate 1 sont restés inchangés.
Un nouveau processus, avec un répertoire d'état vide, a retrouvé ce même reçu et
l'a rejoué avec `replayed=true` : toujours un commentaire et aucune seconde
projection State.

La reprise d'un POST d'audit à réponse perdue est également bornée par une
intention locale durable, indexée par le Project et l'Epic et portant l'identité
exacte du premier audit. Si cet audit reste invisible, un rejeu avec un nouveau
timestamp ou nonce refuse tout second POST. Les tests de transport couvrent ce
cas ; la sonde live ci-dessus a qualifié le rejeu d'un audit visible, et non
cette injection de réponse perdue. L'intention locale ne constitue ni un CAS
fournisseur, ni une garantie d'exactement une écriture entre machines ; le risque
résiduel S1→S2 de PAT-ADR-0006 reste assumé.

Le parcours d'adaptateur requalifie en outre l'identité live du Project privé et
du dépôt canonique lié séparément avant chacun des deux effets fournisseur. Les
tests de transport détachent le dépôt juste avant le commentaire, puis juste avant
State : l'effet concerné est refusé dans les deux cas. Après un refus à la seconde
borne, une reprise requalifiée réutilise le commentaire existant et termine State
sans second POST commentaire. Cette défense est un préflight borné ; elle ne ferme
pas la fenêtre résiduelle entre sa lecture et l'écriture.

Cette recette qualifie le parcours borné exact de PAT-ADR-0006, pas une
transaction GitHub. Le digest de snapshot détecte une dérive observée mais ne
sert jamais de précondition fournisseur. Une écriture concurrente externe entre
la dernière lecture S1 et l'écriture ciblée S2 peut encore être écrasée et
échapper à la détection. Il n'existe ni CAS commun à l'Issue et à l'item Project,
ni verrou distribué, ni exclusion d'un autre writer. Les autres propriétaires,
Projects d'organisation, dépôts publics ou détachés et catalogues de champs
différents restent hors de cette qualification.
