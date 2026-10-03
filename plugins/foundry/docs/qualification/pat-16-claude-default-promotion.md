# Décision de promotion Claude PAT-16

Statut : promotion technique approuvée et appliquée aux défauts du code source ;
review de livraison, CI sur SHA exact et merge Foundry encore requis.
Cette note consigne la décision humaine. Elle ne crée aucune preuve ou receipt.

## Approbation et périmètre

Le 3 octobre 2026, après présentation du tableau concret, des observations natives,
des échecs conservés et du rollback vérifié, le mainteneur a répondu exactement :
« C’est bon ». La proposition examinée portait sur la PR
[70](https://github.com/patobiskoto/patolabs-plugins/pull/70), HEAD avant promotion
`da1434d7df79c28c0793e1a93b96566f9f8d5c36`. L'accord autorise les quatre défauts
Claude ci-dessous sous le contrat limité de PAT-ADR-0013 accepté ;
PAT-ADR-0011/0012 restent des décisions historiques supersédées.

| Tier | Modèle de politique | Effort demandé | Rôle principal |
| --- | --- | --- | --- |
| economy | `haiku-4.5` | `null`, non applicable, aucun effort transmis | scout |
| balanced | `sonnet-5.5` | `medium` | implémenteur, coordinateur |
| frontier | `opus-5.5` | `high` | reviewer |
| apex | `opus-5.5` | `high` | architecte |

Le tier apex et les planchers restent ceux du contrat existant. La recommandation
du coordinateur Claude devient Sonnet 5.5/medium ; le modèle de la conversation
principale et les paramètres personnels ne sont pas reconfigurés. Codex conserve
ses défauts, scopes et preuves PAT-14/PAT-15. Fable 5.1 reste exclu de la qualification
Pro sans crédits supplémentaires. Aucun changement de rôles, permissions,
turn caps, autorité ou règle d'escalade n'est inclus.

## Références observées et budget consommé

Les observations ont été réalisées en mode source, Claude Code 2.1.285,
firstParty, claude.ai Pro observé avant dispatch :

- `a17bf3d5f8d3a56a075ef9a9dcc0fdd2e0f6ccb0` : scout Haiku 4.5 daté
  `claude-haiku-4-5-20251001`, effort non applicable, et architecte Opus 5.5/high,
  fixtures figées passées ; échec implémenteur et reviewer indisponible conservés.
- `ec662ecd3f6e35368d412332118f08d87b853818` : implémenteur Sonnet 5.5/medium,
  fixture isolée Read/Edit vérifiée hors ligne ; reviewer Opus 5.5/high ayant lu le
  diff vérifié et rendu sa revue, 14 AC passés et 2 non couverts à cette référence.

Les identités exactes, compteurs réels parent/enfant, digests et limites restent dans
[`pat-16-native-frontmatter-observations.json`](pat-16-native-frontmatter-observations.json)
et [`pat-16-native-complement-observations.json`](pat-16-native-complement-observations.json).
Le premier parent échoué reste conservé. Le budget total est consommé : sept parents,
six enfants, concurrence un, zéro rejeu de qualification et zéro autorisation de
crédit supplémentaire/API. Aucun nouveau slot natif n'est autorisé par cette décision.
Le prix catalogue natif n'est pas une facture ; aucune économie de quota ou
supériorité comparative n'est établie. PAT-17 conserve le benchmark économique.

## Rollback et livraison

Le rollback de politique vérifié avant promotion a restauré la baseline absente
après la preuve terminale du complément, avec les octets candidats conservés
séparément. Les tests conservent aussi la restauration exacte d'un pin opérateur
historique Sonnet 5. Après promotion, restaurer l'absence de politique sélectionne
les nouveaux défauts. Pour rétablir les anciens défauts, il faut un mapping projet
explicite Haiku 4.5/legacy-low, Sonnet 5/medium, Opus 5/high, Fable 5/high,
ou un rollback du code et de ses profils à la référence précédente. Les pins
historiques restent exacts ; Sonnet 5, Opus 5 et Fable 5 n'ont pas été qualifiés
nativement par ces essais candidats et peuvent être diagnostiqués indisponibles.

Les observations, corpus, traces et anciennes preuves restent à leurs coordonnées
d'origine, y compris leurs statuts antérieurs à la promotion. Elles ne certifient
pas le nouveau diff. Sa review indépendante doit recevoir le contrat PAT-14 complet
et l'observation implémenteur terminée ; la CI doit vérifier les deux sources sur son
SHA exact, puis le lifecycle Foundry doit porter la livraison.

Le [handoff PAT-61/PAT-62](pat-16-pat61-handoff.md) conserve les replays des chemins
Claude affectés sur YouTrack, Linear et GitHub Projects avant le PASS global PAT-61.
PAT-62 conserve l'installation officielle, l'upgrade et les vérifications après
publication. Le mode source ne prouve pas une V1 installée. Chaque client/provider
non testé reste non qualifié.
