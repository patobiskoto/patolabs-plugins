# Contrat de migration Codex 2026-10 (PAT-14)

Ce document est le contrat versionné de préparation de PAT-14. Il ne modifie ni
les défauts de production, ni les règles d'escalade, ni les autorités de
campagne. La promotion éventuelle reste une décision humaine par hôte et exige
une ADR acceptée : FOUNDRY-ADR-0008 gèle les mappings, fallbacks et planchers
existants jusqu'à une décision future explicitement acceptée ;
FOUNDRY-ADR-0019 confirme qu'un changement de défaut est un changement de
politique, même après une comparaison concluante.

## Périmètre, état de référence et réconciliation

État préparé le 3 octobre 2026 sur `bc25c344d7eaccfbe9758fd2ddd28c02232db36b`.
Le binding actif est Linear/PAT, projet `aeb77381-f8aa-48d3-a2a5-e6b2d2773007`,
configuration interne du binding sha256:c61c12688c7139b4095f7dc502b4aa57c314be0527e8418f1723051e9bce9051 et registre sha256:2a246a2ce50803d1b8ff7f0aa6a2dac383cb5f5d6860b50e08613066e07b037e.
Les pages d'intake courantes 1--3 ont le même
snapshot `sha256:eabc7db5d702c3785aa7b1b10b55f8bd0f9822a440dc5a66419e2f19a7f45d94`
et forment une union de 70 issues et 56 entrées `historical_index` (20 + 20 +
16). Le profil d'intake plus ancien ne fait pas partie de cette union.

L'index ADR lu dans Linear contient les identifiants historiques 0001--0027 et
PAT-ADR-0001--0009 : les statuts lus sont conservés dans le manifeste de
qualification. Les corps lus de 0001, 0002, 0003, 0005, 0006, 0008, 0010,
0013--0023 et 0026 contraignent ce contrat. Les notes de livraison PAT-23/PAT-10 rapportent l'import audité des 27 ADR.
La présente vérification relit l'index complet et les corps nécessaires listés
ci-dessus dans Linear ; elle ne rejoue pas l'audit intégral des 27 corps. Le manifeste `linear-selective-migration-manifest.json`, qui
ne porte que treize ADR `archive-reference`, est un ancien instantané de
cutover sélectif et ne démontre donc ni ne contredit cet import ultérieur.

Cette observation ne prétend pas que les relations de supersession ou d'issues
sont connues : PAT-ADR-0003 qualifie `issues`, `superseded_by` et `supersedes`
comme **inconnus / indisponibles**, et non vides. Les enveloppes source
confirment `missing_relations`. Les mentions textuelles restent consultables,
mais aucune relation Linear native n'est inventée. Distinguer aussi le digest
du corps source (`source_body_sha256`) de celui de l'enveloppe/version Linear :
ils attestent des objets différents. Les versions historiques non accessibles
restent une lacune déclarée. Cet état de préparation n'avait changé aucun statut
accepté. La décision distincte a depuis été enregistrée dans Linear comme
PAT-ADR-0010 et explicitement approuvée par le mainteneur le 3 octobre 2026.

Il n'existe aucun repli opérationnel vers YouTrack : l'archive reste seulement
lisible et les preuves terminales qui y vivent restent historiques, avec leur
SHA et leur contexte d'origine.

## Références ADR et corpus figé

Les readbacks Linear pertinents sont `accepted` : 0006 ref
`c5998377-ff22-4420-9acb-47e472b82a2d` SHA `ea28…550b`, 0008 ref
`5fe70e95-9496-47c3-bc1a-375d1a05e2a4` SHA `2f21…654f`, 0015 ref
`f5935b49-cc6f-46a9-a95c-c228f8224bf4` SHA `31f3…2350`, 0019 ref
`050a1ff8-0d0b-496e-b25d-759a1551d23d` SHA `e2d4…9298`, 0020--22 refs
`9a04…31b0`, `e17e…cdfb`, `b7b7…7132`, 0026 ref `affec5e5-72f3-47ed-8818-2208f69ffad2`,
and PAT-ADR-0003 ref `2f767b13-9e36-4374-9585-050abe850b6a` SHA `7394…59c5`.
The full SHA-256 values are in the qualification JSON. These are body digests;
they are not an envelope/version digest.

The audited ADR import records 27 historical ADRs (25 accepted, one deprecated,
one proposed), batch `947291…baed2`, source-manifest `263ab3…ef3a`, and
post-write audit mentionné dans les notes historiques (artefact non relu ici). Its source versions remain unavailable and its
relations are unknown, not empty. This is why raw unchecked boxes in an issue
body are neither proof of failure nor a delivery verdict.

FOUNDRY-ADR-0006/0008 are amended only by the accepted PAT-ADR-0010 decision
for candidate qualification and a per-host default promotion. ADR-0019 remains
the policy for ordinary role-local comparison and promotion. ADR-0020--0022
govern only the separate FOUNDRY-140 full escalation pipeline and are not
silently imported into the ordinary comparison.

The historical corpus is `model-routing-pilot-results-v1.md`, frozen P-01--P-10:
for example P-02 is `FOUNDRY-14`, Codex implementer
`gpt-5.6-terra`/medium, no retry, and its reviewer verdict is PASS on claimed
diff `101ae11`; P-05 is `FOUNDRY-17`, with two retries and final PASS on
`3c957ca`. The summary records ten valid issues and the historical `ADJUST`
verdict, not a candidate result. Before any candidate call, the pair, base/head,
fixture digest, order, decision threshold, one-attempt limit and signed budget
must be frozen. Unknown identity or cost is inconclusive and retains the
baseline; it is not an API or subscription saving.

## Baseline et candidats proposés

Les rôles, quatre tiers, priorités, contextes bornés, indépendance de revue,
planchers et plafond existant d'escalade restent inchangés. Le tableau sépare
la politique candidate de la capacité documentée et de la possibilité réelle
d'invocation.

| Rôle / tier | Ancien mapping (rollback) | Défaut de cette révision | Effort | Observation native |
|---|---|---|---|---|
| Lupin, scout / economy | `gpt-5.6-luna` | `gpt-6-luna` | `low` | smoke terminé, identité hôte observée |
| Eiffel, implementer / balanced | `gpt-5.6-terra` | `gpt-6.1-sol` | `medium` | smoke terminé, identité hôte observée |
| coordinateur / balanced | `gpt-5.6-terra` | `gpt-6.1-sol` | `medium` | recommandation ; conversation principale non reconfigurée |
| Maigret, reviewer / frontier | `gpt-5.6-sol` | `gpt-6.1-sol` | `high` | revue native du SHA gelé terminée ; nouvelle revue du diff final requise |
| Vauban, architect / apex | `gpt-5.6-sol` | `gpt-6.1-sol` | `max` | smoke terminé, identité hôte observée |

Statut de cette révision : implémentation de la promotion technique Codex autorisée,
avec revue indépendante et CI du SHA final encore requises avant merge. Le
[rapport PAT-15](qualification/pat-15-codex-qualification-v1.md) conserve les
coordonnées gelées et les limites ; il ne clôt pas PAT-17.

Claude Code est préservé : Haiku 4.5/low, Sonnet 5/medium, Opus 5/high et
Fable 5/high ne sont ni reconfigurés ni qualifiés dans cette tranche (PAT-16
est post-V1). Le candidat `gpt-6-astra` ne porte aucune présomption de supériorité et
ne devient jamais un défaut apex à partir d'une liste de modèles ou d'une
annonce.

Le 3 octobre 2026, les sources OpenAI officiellement consultées indiquent
`gpt-6.1-sol` avec `low`, `medium`, `high`, `xhigh`, `max`, et `gpt-6-luna`
avec `none`, `low`, `medium`, `high`, `xhigh`, `max`.
([Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
[Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)). Ces pages
documentent une capacité d'API, non le support du client Codex ni l'accès du
compte. Les observations locales `codex-cli 0.155.1` et Claude Code `2.1.267`
ne prouvent pas davantage l'invocation. Toute qualification distingue donc :

1. annonce ou documentation fournisseur ;
2. support du client/hôte avec la version et le mode effectif (Standard/Fast si
   observable) ;
3. accès du compte et invocation réellement observée.

L'hôte peut ne fournir aucune observation d'identité fiable. Une chaîne de
texte produite par le modèle ne prouve jamais le modèle exécuté. En ce cas,
le verdict de cette coordonnée est `inconclusif`, et non « candidat confirmé ».

## Sémantique de compatibilité

`DEFAULT_MAPPINGS` dans cette révision déclare Codex economy
`gpt-6-luna`/`low`, balanced `gpt-6.1-sol`/`medium`, frontier
`gpt-6.1-sol`/`high`, apex `gpt-6.1-sol`/`max`. Un identifiant historique reste
tel quel ; aucune configuration, trace ou override GPT-5.6 n'est réécrit.

[`examples/codex-gpt-6-candidates.json`](../examples/codex-gpt-6-candidates.json)
reste une configuration explicite reproductible des quatre profils, désormais
identiques aux défauts de cette révision. Elle n'est pas chargée automatiquement.
Les quatre smokes autorisés sont terminés ; la livraison du nouveau diff suit
les gates ordinaires de revue et CI exacte, sans cinquième slot de qualification.

[`examples/codex-gpt-5.6-rollback.json`](../examples/codex-gpt-5.6-rollback.json)
reproduit exactement le mapping Codex de `7adc33793cc04dfd5102f1e11a5d47cb6ecc9cd0`
(référence 0.9). Son application explicite au projet est vérifiée avant puis après
modification des défauts par `tests/test_codex_rollback.py` : les overrides anciens
restent prioritaires, les planchers et escalades persistent, aucune preuve n'est
réécrite. Cette vérification est locale et mécanique ; elle n'est ni une
réinstallation réelle ni une preuve de CI sur un SHA de rollback.

Le resolver applique, pour un tier, la précédence demande explicite de
l'utilisateur, rôle/mapping du projet, puis défaut Foundry. Un mapping projet
est champ par champ : `model` et `effort` peuvent provenir de sources
différentes. Une demande directe de modèle n'est pas autorisée pour reviewer ou
architect, ni quand un plancher dynamique est actif, car elle ne peut pas être
classée contre le plancher. Les overrides d'hôte sont seulement signalés avec
un nom sans valeur ; ils ne constituent pas une preuve d'identité ni une
promotion.

Les efforts sont ordonnés seulement dans `(hôte, famille, version de
politique)`. Le code actuel déclare le scope Codex `default` v1 (`low`,
`medium`, `high`, `xhigh`, `max`) et, séparément, les scopes `gpt-6-luna`,
`gpt-6.1-sol` et `gpt-6-astra` v1 avec la même liste plus `ultra`. Il n'existe
pas de scope générique `gpt-6` : une capacité d'une famille ne rend donc pas
un effort valide pour un autre modèle. `ultra` est explicitement inadmissible
pour la délégation, même dans ces scopes, par PAT-ADR-0010 ; `none` est non
applicable à la délégation actuelle parce qu'il n'est dans aucun scope Codex
déclaré. Une option d'effort absente hérite du mapping ; un effort demandé inconnu
ou inadmissible échoue avec son scope et les niveaux acceptés : il ne
devient jamais `low`. Les niveaux ne sont pas comparables d'une famille ou d'un
hôte à l'autre.

Les gates demeurent reviewer >= frontier et `high`, architect >= apex et
`high`. L'indisponibilité d'un modèle fait suivre le chemin de fallback du
resolver : un gate monte et ne descend pas sous son plancher ; un rôle sans
plancher dynamique essaie les tiers inférieurs. L'effort explicitement demandé
reste indépendant de ce fallback et doit être valide dans le scope du modèle
final. Aucun fallback intergénérationnel, changement de gate ou accès supposé
n'est inféré hors de ce chemin déclaré.

Il n'y a pas de `.foundry/model-routing.json` dans ce dépôt aujourd'hui.
Cette tranche déclare les mappings candidats, scopes et vocabulaire de
télémétrie de façon cohérente. Un scope explicite par modèle (`gpt-6-luna`,
`gpt-6.1-sol` ou `gpt-6-astra`) sélectionne actuellement son effort ; il ne
déclare pas une identité de télémétrie. `telemetry.KNOWN_MODELS` contient les
modèles GPT-5.6, Claude et les trois identifiants GPT-6 déclarés. Lors de la
préparation d'une invocation Codex, la façade ajoute
toutefois le `route.model` déjà résolu à son vocabulaire d'observation : une
demande directe hors gate peut donc conserver son modèle GPT-6 résolu sans une
déclaration projet qui en serait l'unique source. Ce complément ne déclare pas
une identité d'exécution, ne rend pas un suffixe arbitraire acceptable et ne
remplace pas une politique projet pour un mapping. Les journaux historiques
signés avec leur `effort_scope` enregistré restent lisibles après retrait d'une
déclaration.


Les alias Claude historiques haiku/sonnet/opus/fable gardent leur traduction
Foundry ; les identifiants exacts épinglés GPT-5.6 ne sont jamais remappés en
GPT-6. Les restrictions de modèles et plafonds d'effort sont des contraintes
de l'hôte, distinctes de la politique Foundry : un refus ou plafonnement
sous le plancher d'un gate ne peut fournir une qualification positive.
Sans observation fiable du modèle/effort exécuté, la conformité reste inconnue.
Aucune restriction utilisateur/administrateur n'est réécrite pour réussir un test.
La documentation [Claude model-config](https://code.claude.com/docs/en/model-config),
consultée le 3 octobre, distingue les alias, les listes de modèles autorisés
et les limites d'effort. Son comportement courant n'est pas réputé testé
sur le client local 2.1.267 ; PAT-16 conserve cette qualification.
[Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), consulté
le même jour, documente low/medium/high/xhigh/max côté API ; son accès
Codex effectif reste non qualifié.

## Qualification bornée et preuves

Le corpus est réel et historique : `model-routing-pilot-results-v1.md` est
conservé comme corpus de comparaison et non réécrit. Il conserve les tickets,
les verdicts de revue, retries, escalades, coûts et le SHA qui les a produits.
Ses verdicts n'établissent pas l'identité, la qualité ou le coût des candidats
GPT-6. Chaque comparaison isole un rôle, reprend les mêmes entrées et barème,
et part d'une fixture isolée conformément à ADR-0019 ; ADR-0020--0022
restent limitées à leur comparaison de pipelines.

Pour chaque paire baseline/candidat, la campagne consigne : SHA et worktree
isolé, snapshot du corpus, hôte/client/version/mode, route demandée et route
résolue, identité hôte lorsqu'elle est fournie de manière fiable, scope et
version d'effort, disponibilité, statut/failure class, durée, corrections
Eiffel, re-reviews Maigret, escalades, verdict de contrôle indépendant et
résultat de livraison. L'observer local ne reçoit que sa projection assainie :
son `run_id` aléatoire de 128 bits ne porte ni utilisateur, ni machine, ni
issue. ADR-0015 sépare cet observer de son lecteur hors-ligne de journaux host :
l'observer ne lit jamais de fichiers ni ne route, le lecteur explicite est postérieur,
sans réseau ni mutation. La provenance acceptée est `client_observed`,
`host_reported`, `pricing_derived`, `unavailable`; toute valeur indisponible demeure
`null`/`unavailable`.

Un rôle de contrôle ne peut être qualifié que si son gate a reçu le packet
borné et indépendant attendu, n'a pas été remplacé par un sous-agent non
déclaré, conserve ses planchers et rend un verdict traçable. Une substitution,
un refus de l'hôte, une erreur de configuration, une identité non attestée ou
une métrique indispensable indisponible arrête cette coordonnée comme
inconclusive. On ne la transforme pas en PASS, zéro coût, ou preuve d'économie.

Avant tout essai externe, l'opérateur doit approuver une autorisation distincte
et signée, liée au corpus, SHA, hôtes, coordonnées, plafond par appel/par hôte/
total et date d'expiration (ADR-0010). L'enveloppe proposée, non autorisée, est
Codex seul, **10 USD total**, **2 USD par appel**, une tentative par paire,
expiration 24 h après signature. L'autorisation courante est zéro USD et aucun
essai. Le protocole concret ci-dessous concerne uniquement Maigret ; les verdicts possibles sont conformité
déterministe + gate indépendant sans régression, ou `inconclusif` et baseline
conservée. ADR-0019 applique son régime sans benchmark supplémentaire à un
candidat prouvé moins cher; sinon la comparaison reste bornée, par rôle, à
entrées/barème identiques. ADR-0020--0022 ne s'appliquent que si le pipeline
FOUNDRY-140 est choisi, pas à cette comparaison ordinaire. Les quotas inclus Codex, crédits additionnels, coût API estimé,
tokens et coût reporté par fournisseur sont des champs distincts ; un prix ou
quota inconnu ne démontre aucune économie. Une autorisation de campagne ultérieure doit lier ses plafonds positifs
explicitement, sans
conversion points ou tokens -> quota.

La qualification technique urgente acceptée par PAT-ADR-0010 n'est pas ce
benchmark reviewer. Ses quatre sessions incluses sont entièrement gelées dans
[`qualification/pat-14-native-smoke-v1.json`](qualification/pat-14-native-smoke-v1.json) :
un packet par tier (scout, implementer, reviewer, architect), fixture/source
digesté, verdict attendu et vérificateur hors ligne. Elles sont exactement
quatre, vingt minutes chacune, sans slot supplémentaire ni retry. Le reviewer
est la revue normale du premier HEAD immuable de la PR PAT-15 qui satisfait les
checks déterministes : URL, base, head, digest du diff et résultats de checks
sont gelés avant les quatre dispatchs, puis claimés par le mécanisme normal.
Il ne réutilise ni PAT-85, ni son hash, ni une ancienne claim, et ne crée aucun
receipt artificiel. Son verdict reste la gate réelle de cette PR ; il n'est pas
une preuve de qualité comparative ou de compétence générale. L'implementer ne touche qu'une
fixture temporaire isolée et son patch est contrôlé hors ligne. Le smoke ne
mesure aucune qualité comparative, économie ni compétence générale. Les trois
slots statiques ne sont pas des gates PR ; la revue PAT-15 reste sa gate
ordinaire, et les tests et CI exacts de PAT-15 restent requis.

Le record natif d'une session distingue : la route demandée/résolue par
Foundry, le modèle/effort transmis à l'hôte, l'identité réellement observée par
l'hôte, le statut et une identité de contexte opaque. Le modèle transmis n'est
pas une identité exécutée. Seules des métadonnées natives liant la session au
modèle et effort exacts peuvent attester celle-ci ; nom d'agent, plan,
préfixe de famille, texte auto-déclaré ou receipt Foundry sont insuffisants.
Si l'hôte ne fournit pas cette preuve, le coordinateur annule ou marque la
coordonnée `unavailable` et conserve la baseline, sans substitution.

Les stops sont : autorisation absente/invalide/expirée ; dépassement ou risque
de dépassement d'un plafond ; SHA, corpus ou worktree non conformes ; gate,
plancher ou indépendance non respectés ; client/compte/modèle indisponible ;
identité non fiable quand elle est requise ; métrique nécessaire inconnue ; ou
comparaison Sol/Astra non appariée. Astra ne peut être appelé que par une
escalade explicitement motivée et sa comparaison doit porter sur le même rôle,
effort pertinent, corpus, hôte/mode et barème que Sol. Il n'existe aucun retry,
correction ou escalade automatique dans cette préparation.


### Corpus minimal disponible et barème avant essai

Les anciens P-01--P-10 constituent le contexte historique : leurs références
abrégées ne suffisent pas pour démarrer un rejeu. Le petit corpus concret de
revue disponible ici est PAT-85, avec le même SHA de base
f7fb4e14f272de78d7f848b3c23e4a963a623087 :
- head 3bbee7cdc61adeb5b1d5f5790a5a411e1b0d6413,
  diff 27e7644c9acba8572bf7ee1e726254baa04881ff9f8e765c43ad2ec9b890a2df :
  BLOCK attendu, finding sur titres/tableaux non qualifiés ;
- head 75c0116bfabbfef8d07c2afaebe558a096e2ad53,
  diff 08fe555272511dc404ae9f70fdc858aec5260c073cb8d724b1cf49b34000fba8 :
  PASS attendu après correction. Les objets Git ont été vérifiés présents.

Protocole reviewer proposé : titulaire Sol 5.6/high puis Sol 6.1/high, sur
les deux diffs, packets et barème identiques ; maximum quatre sessions, une
par modèle/cas, zéro retry de campagne, arrêt au premier résultat incorrect
ou indisponible. Succès technique = 2/2 verdicts corrects et finding bloquant
réel identifié, zéro faux blocage sur le diff corrigé. Ce corpus très petit
ne prouve pas une supériorité générale. Si Astra doit être comparé, il exige
un plan distinct Sol 6.1/high versus Astra/high, mêmes deux cas et plafond
quatre sessions, aucune invocation supplémentaire sous le premier plan.
Aucun benchmark scout/coordinateur n'est ajouté : ADR-0019 l'interdit.
L'implementer est vérifié sur l'issue et les tests PAT-15 ; l'architecte
reste soumis à son plancher et à une invocation bornée normalement routée,
sans benchmark inventé pour ce rôle.

Plafonds proposés par plan reviewer : Codex 10 USD total/par hôte, 2 USD
par session/appel, quatre sessions maximum, expiration 24 h après signature.
Les plafonds ne sont pas une autorisation. Un coût natif nécessaire inconnu
ou un préflight incapable de borner un appel refuse le benchmark ; aucun
prix API ne remplace une mesure de quota d'abonnement.

## Intégration, promotion et rollback

Le **coordinateur d'intégration PAT-13** est l'unique propriétaire du séquencement
des interfaces partagées M2/M3 : déclaration de modèles/scopes, façade Codex,
télémétrie, tests de compatibilité et documentation. M2 et M3 ne modifient pas
ces surfaces en parallèle sans son ordre sérialisé et son readback de SHA.

Pour la tranche urgente PAT-15, PAT-ADR-0010 distingue la conformité
technique d'une preuve d'économie : une promotion Codex peut être proposée après
les tests déterministes, au maximum quatre smoke sessions natives incluses
dans l'abonnement, une par tier, vingt minutes par session, zéro retry de
qualification, zéro crédit additionnel/API, revue indépendante, CI exacte et
rollback vérifié. Un refus, une substitution non attestée ou un plancher non
respecté conserve la baseline. Cette exception explicite à la qualification
économique d'ADR-0019 est acceptée, mais son exécution reste bornée. Elle ne permet
aucun benchmark payant et ne garantit aucun gain de quota. La comparaison
reviewer ci-dessus reste distincte, facultative et soumise à son autorisation
signée ; elle n'est pas un préalable caché exigeant PAT-17. Conserver GPT-5.6 est un verdict valide. Si un hôte échoue ou reste
inconclusif, il conserve sa baseline et les preuves de l'autre hôte ne sont pas
extrapolées. Le rollback est par hôte et consiste à rétablir le mapping versionné
précédent, vérifier résolution, planchers, traces et CI sur le SHA de rollback,
puis enregistrer l'écart sans réécrire les journaux ou preuves historiques.

## Décision approuvée

Le seul amendement d'architecture nécessaire est le contrat versionné
[`qualification/pat-14-proposed-adr.md`](qualification/pat-14-proposed-adr.md),
enregistré dans Linear comme PAT-ADR-0010 (accepted) : il autorise une qualification
Codex bornée et une promotion par hôte selon ce contrat. Il ne contourne pas
ADR-0008 ou ADR-0019 et n'élargit pas Claude, Astra, l'autorité de campagne ni
la promotion automatique. Les preuves déterministes, le smoke natif, la revue,
la CI exacte et le rollback restent les gates avant tout changement de défaut.

## Statuts documentaires PAT-15

- `DEFAULT_MAPPINGS` Codex et recommandation coordinateur : mis à jour ici et dans `model-routing.md`.
- Exemple de rollback GPT-5.6 : ajouté, version de référence et vérification locale documentées.
- Rapport de qualification : ajouté avec observations natives, digests gelés et limites économiques.
- Commentaire d'autorité `ultra` : corrigé vers PAT-ADR-0010 ; la restriction publique existante ne change pas.
- CLI, précédence, scopes, télémétrie, gates et autorités : aucun nouveau comportement public dans cette phase ; les contrats déjà documentés demeurent applicables.


## PAT-16 Claude candidate preparation and rollback

The Claude mappings described above are the incumbent policy, not a native compatibility
claim. PAT-ADR-0011's limited accepted amendment permits the opt-in candidate example
`examples/claude-candidates-pat16.json`: Haiku 4.5 with null/not-applicable effort,
Sonnet 5.5/medium and Opus 5.5/high for frontier and apex. Fable 5.1 remains excluded
on Pro from the zero-credit envelope. Codex defaults, scopes and PAT-14/15 evidence
are unchanged. No example is copied into `.foundry/model-routing.json` automatically.

Save original project policy bytes (or record that no policy file exists) before opt-in.
After the bounded qualification, restore exactly those bytes or remove only the newly
created policy file. Keep personal settings and primary-session model intact. The
rollback test resolves all four incumbent roles again after restoration, including an
operator's explicit historical pin. Incumbent defaults remain Haiku4.5/legacy-low,
Sonnet5/medium, Opus5/high and Fable5/high. Their invocation now preserves exact version
IDs; legacy Haiku low is requested intent with no transmitted effort. An unsupported
incumbent is unavailable, never guessed. Frozen proposal files remain unchanged.

Document statuses: model declarations, alias compatibility, null effort, effort-free
profiles, value-free force/settings detectors, native-observation limits and telemetry/
offline pricing vocabulary are updated in `model-routing.md`; the opt-in/rollback
surface is updated here and in the example; R7 is updated in byte-identical
`AGENTS.md`/`CLAUDE.md`. No new CLI verb, role/permission/turn cap, Codex effort scope,
price grid or passive telemetry schema is introduced, so documentation for those
specific surfaces needs no change.

Deterministic checks precede frozen native qualification, independent review,
exact-SHA two-source CI, and an explicit human default-promotion decision. Native
qualification remains pending; all untested client/provider pairs remain unqualified.
Loaded native Foundry ref/version, precise child model/effort and actual bound usage
must be observed by the coordinator, never inferred from this source preparation.


The immutable step-0 proposal remains a historical review copy. The governing tracker
record is accepted PAT-ADR-0011, revision v2, as read back by the coordinator after
PAT-86 repair and explicit limited acceptance. Its frozen Claude contract SHA-256 is
`8aa613503dbf04326d6c01e011d9dedef21c21036932be2577624a922df3fb78`.
Approval authorizes the bounded candidate preparation/qualification envelope; this
source change still retains incumbent defaults and has candidate-only status pending
native evidence, independent review, CI, verified rollback and promotion decision.


PAT-16 interface remediation: the first original-envelope scout slot failed before
child launch because Claude Code 2.1.285 rejected a full identifier in Agent `model`.
The three remaining original slots were not run. No native profile was qualified or
promoted. The static remedy selects generated preloaded full-ID profiles and omits
wire `model`; aliases use only the observed supported enum. Original frozen proposal
bytes and failed evidence remain preserved. See
`qualification/pat-16-native-interface-observation.md`; another native attempt requires
explicit approval of a fresh interface/corpus/envelope, never an implicit retry.
The generation/frontmatter/wire distinction is updated in `model-routing.md` and R7;
role tools, turn caps, review claims, passive telemetry schema and Codex scopes remain
unchanged. The existing conformance/guard/correlation tests cover the suffixed profiles.


## PAT-16 final migration boundaries and complement observations

The current accepted campaign constraint is PAT-ADR-0013; PAT-ADR-0011 and
PAT-ADR-0012 are superseded historical decisions, not current independent approvals.
The immutable original contract and earlier failed lots remain evidence.

An alias-only `FOUNDRY_CLAUDE_AVAILABLE_MODELS=haiku,sonnet,opus` cannot attest the
version-pinned defaults. List the actually available canonical/full version IDs, or
explicitly choose host aliases in the project mappings. Redundant built-in translations
such as `claude_models: {"opus-5": "opus"}` must be removed to preserve the pin;
choosing `model: "opus"` explicitly is a separate alias intent, never a migration default.
A direct Haiku request inheriting medium/high must instead use economy or a project
mapping with null effort. Headless runtimes now omit Haiku effort, like the Agent path.

The historical FOUNDRY-43 measurement harness retains its frozen 2.1.224 alias corpus
and refuses today's default version pins before launch. An intentional historical
replay requires an explicit alias mapping and the original private frozen corpus;
its bindings, traces and prices are not rewritten. That corpus is absent from this
public checkout, so its full opt-in suite cannot be validated here. A public isolated
unit test verifies the actionable prelaunch refusal without fabricating corpus evidence.

The complement at `ec662ecd3f6e35368d412332118f08d87b853818` exercised Sonnet 5.5/medium
worker Read/Edit on the exact isolated fixture successfully, and Opus 5.5/high read the
verified diff and completed an independent review. The report passed 14 AC and left
promotion/rollback/handoff AC 15–16 not covered; it is not merge approval. See
`qualification/pat-16-native-complement-observations.json`. The reviewer first read the
implementer observation before it existed; final delivery review must receive that
completed observation and PAT-14's complete contract. Earlier failures remain recorded.
The total budget is consumed: seven parents, six children, no qualification retries.

The absent project-policy baseline was restored after terminal review proof, preserving
candidate bytes separately. Personal settings and installed cache were unchanged.
Policy rollback restores incumbent routing within this new code; it does not revert
new full-pin transmission semantics. A code rollback must restore the previous code ref
and profile inventory together. Incumbent Sonnet 5, Opus 5 and Fable 5 full pins have not
been natively qualified by these candidate trials. PAT-61 must retain that limit and
replay affected candidate cells before V1; PAT-62 retains post-install checks.
Explicit human promotion remains pending, and no economic superiority is asserted.

Document statuses for this correction: the headless effort/compatibility/diagnostic
surfaces are updated here and in `model-routing.md`; offline terminal-snapshot/date
semantics are updated in `cost-attribution.md`; R7 source pointers are updated in
byte-identical AGENTS.md/CLAUDE.md. Historical corpus files, pricing, roles, tool caps,
Codex mappings and the passive telemetry schema are unchanged.
