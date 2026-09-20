# Per-locale metadata

`fastlane deliver` reads one directory per App Store localization under
`fastlane/metadata/<locale>/`. Each holds the store text for that language:

```
fastlane/metadata/
  en-US/
    name.txt                 # app name (≤30 chars)
    subtitle.txt             # ≤30 chars — ASO-relevant
    description.txt          # full description
    keywords.txt             # comma-separated, ≤100 chars — this IS your ASO
    release_notes.txt        # "What's New" for the current version
    promotional_text.txt     # ≤170 chars, editable without a new version
  fr-FR/ …
  es-ES/ …
```

`release_notes.txt` is the only file the `ship-ios:release` skill rewrites every version; the
rest change rarely. There is no default language — the set of locale dirs IS the
configuration, and it must match the `languages([...])` list in `Snapfile`.

This `metadata_example/` folder is illustrative; the real tree lives in the app repo
and is created by `ship-ios:setup` from the locales you declare.
