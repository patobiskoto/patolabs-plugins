# Tests de campagne de benchmark et de POC

FOUNDRY-127.

## Pourquoi ils sont hors de la suite par défaut

FOUNDRY-ADR-0019 a formellement écarté l'approche par matrice de coordonnées complète
(modèle × effort × contexte × répétitions) au profit d'une comparaison bornée contre
l'historique pour décider d'une promotion de modèle sur un rôle. Les campagnes de
benchmark et les POC versionnés sous `benchmarks/` (`foundry-35`, `foundry-41`,
`foundry-43`, `foundry-45` à `foundry-69`) sont les artefacts de cette approche
écartée : dix-neuf campagnes, aucune n'a produit de résultat exploitable (mesures
retirées, non exécutées, `null`, `unavailable`, fixtures synthétiques seulement,
`inconclusive`…).

Les tests qui revalident ces artefacts (recalcul d'agrégats, rejeu de harnais figés,
contrats de protocole gelés) n'exercent pas de code produit exécuté en production : ils
prouvent que des preuves déjà closes d'une méthode abandonnée restent bit-à-bit
identiques à elles-mêmes. Les faire tourner à chaque PR coûtait l'essentiel du temps de
la suite (des tests individuels de 15 à 28 secondes, l'essentiel des 538 secondes
mesurées avant ce ticket) sans protéger de régression sur un comportement livré.

Rien n'est supprimé : les artefacts et les fichiers de test restent au dépôt,
exécutables à la demande.

## Comment les rejouer

Tous les tests concernés portent le marqueur pytest `benchmark_campaign`, déclaré dans
`plugins/foundry/pytest.ini`. Le marqueur est posé à la collecte par
`tests/conftest.py` (`pytest_collection_modifyitems`), pas par une ligne `pytestmark`
dans chaque fichier de test : plusieurs de ces fichiers sont hashés octet pour octet
comme preuve « v1 baseline » par d'autres harnais de campagne sous `benchmarks/` ; les
modifier changerait ces octets et ferait échouer la campagne même que le marqueur sert à
isoler.

Pour les fichiers de campagne complets, `conftest.py` les ignore avant même leur import
si l'expression `-m` ne mentionne pas explicitement `benchmark_campaign` (y compris la
commande par défaut, `pytest -q -m "not integration" tests`). C'est nécessaire car
certains harnais importent `cryptography` ou `huggingface_hub` au niveau du module,
avant que pytest puisse désélectionner leurs items. Les fichiers qui mélangent une
couverture produit et benchmark restent collectés puis sont désélectionnés test par
test.

Pour les rejouer explicitement :

```
cd plugins/foundry
pytest -q -m benchmark_campaign tests
```

Pour tout exécuter, campagnes comprises :

```
cd plugins/foundry
pytest -q -m "benchmark_campaign or not integration" tests
```

Certains de ces tests (les harnais figés v3/v4) importent `huggingface_hub` et
`cryptography`, non requis par la suite par défaut ; installez-les si besoin
(`pip install huggingface_hub==1.14.0 cryptography==45.0.7`).

## En CI

Le job par défaut (`foundry`) ne récupère plus les refs figées
(`refs/foundry/freeze-v2` à `v4`, `evidence-v4`) : elles ne servent qu'à revalider ces
campagnes. Un job séparé, `foundry-benchmark-campaigns`, se déclenche manuellement
(`workflow_dispatch`, entrée `run_benchmark_campaigns: true`) ; lui seul récupère ces
refs et exécute `pytest -q -m benchmark_campaign tests`.

## Quels fichiers portent le marqueur

Le marqueur est posé fichier par fichier (ou test par test quand un fichier mélange du
produit et du benchmark, par ex. `tests/test_local_scout.py`) sur ce que le test exerce
réellement, pas sur son nom. En particulier :

- `tests/test_campaign_*.py` testent `foundry.campaign_coordinator` et les modules
  associés — l'orchestration produit des campagnes Epic (ADR-0013, ADR-0017), utilisée
  par `command_runtime`/`command_worker` en production. Ce n'est **pas** un benchmark ;
  ces fichiers restent dans la suite par défaut malgré le nom « campaign ».
- Les fichiers qui chargent un script sous `benchmarks/foundry-NN/` via
  `importlib.util.spec_from_file_location`, ou qui exercent `foundry.benchmark_evidence`,
  `foundry.measurement_harness[_v2]` ou `foundry.local_benchmark` — des modules qui ne
  sont importés que par ces tests et jamais par le runtime produit — portent le
  marqueur.
