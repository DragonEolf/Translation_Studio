# Version checkpoints

## `checkpoint/span-rendering-2026-10-17`

Working version before experimenting with paragraph layout and inline formatting:

- Selectable PDF text retains individual span boxes and formatting.
- Bodhan translations use validated XML batching with smaller-batch fallback.
- Caching is disabled by default.
- The local upload/download interface supports all 26 language/script options.
- Downloads use the original filename and target language.
- Noto fonts, tests, and experiment scripts are included.

Uploaded documents, generated output, the local database, and credentials remain
local and are excluded from Git.

To inspect or run this checkpoint in a separate checkout without changing ongoing
work:

```sh
git worktree add ../Translation_Studio-checkpoint checkpoint/span-rendering-2026-10-17
```

The checkpoint does not include paragraph reflow or translated inline formatting
tags. The shared-size comparison script is an experiment, not production behavior.
