## Contexte

Le contrat `foundry.tracker-contract.v1` (PAT-53, `plugins/foundry/docs/tracker-contract.md`, mergé en `3a4ea97`) exige que chaque parcours cœur V1
atteigne `supported` sur YouTrack, Linear et GitHub Projects, et son critère 3 impose
deux règles : ne jamais présenter une lecture avant écriture suivie d'une relecture
comme une exclusion des écritures concurrentes, et faire passer toute modification
d'invariant durable par une nouvelle ADR explicitement acceptée avant le code concerné.

Faits établis dans le code actuel :

- **YouTrack n'offre aucune précondition CAS.** Le commentaire de classe le dit
  (`youtrack.py:109-111`) et `update_body` le documente : « A concurrent non-Foundry
  writer can still win between requests » (`youtrack.py:396-402`). `update_body`
  sérialise les écrivains Foundry locaux par `flock` (`_body_lock`, `youtrack.py:371-390`),
  relit, refuse une divergence, écrit une fois et relit (`youtrack.py:435-448`). En
  revanche `update_fields`, `set_state` (qui délègue à `update_fields`) et `link` sont des
  POST inconditionnels, sans lecture préalable ni relecture comparée
  (`youtrack.py:345-364`), et `add_comment` est un POST non idempotent
  (`youtrack.py:366-369`).
- **Linear refuse le remplacement en place d'une issue existante.** Invariant du module :
  « Linear does not expose a compare-and-swap precondition for existing-issue
  replacement. Those writes are therefore unavailable: a local lock or a readback cannot
  prevent an external writer from being overwritten. » (`linear.py:8-11`). En
  conséquence `update_fields` (`linear.py:2455-2470`), `update_body` pour une issue
  (`linear.py:2856-2868`), `sync_acceptance_body` (`linear.py:2870-2882`) et `link` en
  `subtask-of`/`parent-of` (`linear.py:2770-2774`) lèvent toujours ; `set_state`
  n'accepte que `in-progress`, `review` et `done` (`linear.py:2481-2484`).
- **Linear dispose déjà de mécanismes non écrasants.** Les transitions de cycle de vie
  et la complétude des AC sont des commentaires append-only dont l'identifiant est dérivé
  de manière déterministe d'un digest SHA-256 de `(schema, operation, issue[,
  generation])` (`_lifecycle_marker`, `linear.py:1349-1388`) ; un rejeu exact converge
  sur le même commentaire, un contenu différent au même emplacement est refusé
  (`linear.py:1926-1928`, `1946-1953`) ; `Issue.ac_done` est dérivé de cette projection,
  jamais du texte des cases natives (`linear.py:2211-2217`). Les ADR Linear sont des
  Documents append-only à identifiant déterministe par `(project_id, adr_id, sequence)`
  (`linear.py:337-338`), vérifiés octet par octet à la relecture
  (`_create_exact_adr_document`, `linear.py:3298-3335`), conformément à PAT-ADR-0002.
- **La clôture d'Epic est aujourd'hui définie comme atomique.** Le port exige qu'un
  fournisseur « lock the parent graph, compare the exact parent version/type/AC snapshot
  and the complete required-child id/version/state set, then persist both `done` and a
  replayable audit receipt in one transaction » (`base.py:262-276`), et
  `write.close_epic` la décrit comme « an atomic provider graph operation »
  (`write.py:371-376`), refusée avant tout appel fournisseur sans
  `epic_closure_supported` (`write.py:377`). Seul DevHub, hors V1, l'implémente
  (`devhub.py:220-223`, `825`). YouTrack le refuse explicitement : « A read-then-command
  emulation would race » (`youtrack.py:106-108`) ; Linear ne surcharge pas ce port.
- **FOUNDRY-ADR-0017** fait de la clôture d'Epic le porteur du verdict humain, qui « doit
  enregistrer un receipt de validation humaine explicite, horodaté et lié à l'ensemble
  exact des enfants terminaux », et fait reprendre au cadenceur l'état depuis le tracker,
  git et le code-host, en réutilisant des « receipts idempotents ». Elle ne dit rien de
  l'atomicité de l'écriture fournisseur et n'accepte aucune fenêtre de course.
  **FOUNDRY-ADR-0013** impose que la clôture parent passe par `close-epic`, sans
  fast-path.
- GitHub Projects v2 n'est pas qualifié (`ghprojects.py` est un stub ; PAT-65).

Fermer PAT-55 et PAT-56 sur Linear inverserait l'invariant de `linear.py:8-11` ; fermer
PAT-69 sur YouTrack/Linear remplacerait l'invariant atomique de `base.py:262-276` et
`write.py:371-376` par une garantie plus faible. Ces trois changements relèvent d'une
même question — quelle garantie minimale V1 accepte sans CAS fournisseur — et sont
décidés ensemble ici.

## Décision

**1. Vocabulaire.** Sans CAS fournisseur, Foundry ne revendique qu'une *détection bornée*.
Aucune documentation, docstring ou message ne la qualifie d'« exclusion », de « CAS » ou
de « verrou ». Un verrou local (`flock`) n'est qu'une optimisation de contention entre
processus Foundry d'une même machine.

**2. Socle commun** de toute mutation d'un enregistrement existant sans CAS :

- **S1 — relecture fraîche** de l'instantané attendu (les seules valeurs modifiées, ou le
  corps entier) immédiatement avant l'écriture, jamais depuis un cache de session ;
  refus avant tout effet en cas de divergence ;
- **S2 — une seule écriture**, sans retry automatique ou silencieux ;
- **S3 — relecture de vérification** après l'écriture ;
- **S4 — échec fermé** (erreur de conflit typée, aucun succès rapporté) sur toute
  divergence, y compris une réponse ambiguë que la relecture ne résout pas ;
- **S5 — sûreté au rejeu** : une écriture que la reprise peut rejouer après une réponse
  ambiguë converge sur le résultat déjà appliqué (constaté par lecture, ou par un
  identifiant déterministe dérivé de ses coordonnées canoniques) ou échoue fermée ;
  jamais de doublon, de double transition ni d'écrasement ;
- **S6 — préférence non écrasante** : là où le fournisseur permet un enregistrement
  append-only à identifiant déterministe, il est préféré à un remplacement en place.

**Risque résiduel, nommé tel quel** : une écriture externe qui atterrit entre S1 et S2
est écrasée, et S3 ne la voit pas (la relecture montre la valeur de Foundry). Le socle
détecte les divergences antérieures à S1 et postérieures à S2 ; il n'exclut pas la
fenêtre S1→S2.

**3. Par famille de parcours.**

- **Grooming (champs, corps, parent d'une issue existante).** YouTrack : S1-S4 sur
  chaque écriture, champ par champ. Linear : le remplacement en place est **autorisé sous
  S1-S4** et le seul risque résiduel ci-dessus, pour les champs normalisés (y compris les
  états `backlog`/`ready`/`blocked`/`dropped`), le corps et le parent ; cette décision
  **amende explicitement** l'invariant de `linear.py:8-11`, dont le docstring devra
  énoncer la nouvelle garantie et son risque résiduel.
- **État des AC.** YouTrack : `sync_acceptance_body` existant (S1-S4, `youtrack.py:450-466`)
  suffit. Linear : la projection append-only liée à la preuve (`project_acceptance_proof`,
  `linear.py:2510-2562`) est l'autorité V1 de complétude des AC ; la synchronisation en
  place des cases natives n'est **pas** requise pour V1 et reste refusée. PAT-56 ferme la
  ligne AC de Linear par cette projection, sans écriture en place.
- **Projection des statuts.** Linear : marqueurs append-only existants pour
  `in-progress`/`review`/`done` (S1-S6 déjà tenus) ; les autres états relèvent du
  grooming. YouTrack : `set_state` sous S1-S5, avec l'état prédécesseur attendu relu avant
  l'écriture — un rejeu trouvant déjà l'état cible converge, un état tiers échoue fermé.
- **Reprise.** Toute écriture rejouable porte S5. Les notes libres (`add_comment`) ne
  portent aucun état décisionnel : leur doublon après un rejeu ambigu est toléré, elles
  ne sont jamais réessayées silencieusement ni lues comme une autorité d'état.
- **Clôture d'Epic.** Sur YouTrack et Linear, `close_epic` peut réussir uniquement par :
  relecture fraîche du graphe complet (parent, tous les enfants requis, leurs états et
  preuves AC) ; une seule écriture sur le parent ; un reçu append-only à identifiant
  déterministe dérivé de (parent, ensemble exact des enfants terminaux, séquence de
  clôture), portant le verdict humain horodaté exigé par FOUNDRY-ADR-0017 ; relecture ;
  échec fermé sur tout enfant ajouté, rouvert ou modifié, audit manquant ou réponse
  ambiguë. Cette décision **amende explicitement**, pour ces deux fournisseurs,
  l'invariant « in one transaction » / « atomic provider graph operation » de
  `base.py:262-276` et `write.py:371-376` ; DevHub conserve sa garantie transactionnelle.
- **GitHub Projects.** Aucune garantie n'est présumée. PAT-65 qualifie l'API ; à défaut
  d'une précondition fournisseur réellement qualifiée, ce socle s'applique.

**4. Hors de portée.** Cette décision ne modifie ni FOUNDRY-ADR-0002 (preuve CI), ni
FOUNDRY-ADR-0013 (pas de fast-path), ni FOUNDRY-ADR-0017 (verdict humain porté par
l'Epic), ni PAT-ADR-0002 (ADR Linear append-only byte-exactes).

## Conséquences

- PAT-55 et PAT-56 (Linear) et PAT-69 (YouTrack, Linear) peuvent être implémentés
  contre cette décision, et seulement après son acceptation.
- Les lignes YouTrack de PAT-55/PAT-56 élèvent des écritures aujourd'hui aveugles au
  socle sans inverser d'invariant énoncé.
- Les cellules correspondantes du contrat passent à `supported` uniquement lorsque le
  code et ses tests prouvent le socle ; le contrat, les docstrings et
  `skills/close-epic/SKILL.md` nomment le risque résiduel.
- Toute garantie future plus forte (précondition fournisseur réelle) ou plus faible
  passe à son tour par une nouvelle ADR.

## Alternatives écartées

- **Garder le refus Linear et l'absence de clôture d'Epic** : laisse trois parcours cœur
  V1 en `gap` sans échéance.
- **Émuler un CAS par verrou local ou par relecture, présenté comme exclusion** :
  contraire au critère 3 ; un verrou local ne protège que les processus Foundry.
- **Retry automatique après divergence** : masquerait un conflit réel sous une contention
  apparente.
- **Synchronisation en place des cases AC sur Linear** : écrase le texte que la
  projection append-only rend déjà inutile pour la décision de merge.
- **Attendre une capacité fournisseur future (transaction, précondition de version)** :
  non qualifiée ; reporte des parcours cœur sans date.
