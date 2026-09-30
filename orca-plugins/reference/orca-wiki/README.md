# OrcaSlicer wiki snapshots

Saved from the official wiki on 2026-09-29, kept because the plugin system is
new and the online pages move.

| File | Source |
| --- | --- |
| `Plugin Development - OrcaSlicer Wiki.pdf` | <https://www.orcaslicer.com/wiki/developer_reference/plugin_development/plugin_development> |
| `Getting Started - OrcaSlicer Wiki.pdf` | <https://www.orcaslicer.com/wiki/plugins/plugins_getting_started> |

## The one thing these settled

There is **one** plugin picker, not two:

> | Type | Where it's invoked | Pattern |
> | --- | --- | --- |
> | `slicingPipeline` | `Print.cpp` **and** `PostProcessor.cpp` | resolve the preset's capability refs, cast to `SlicingPipelinePluginCapability`, build `SlicingPipelineContext`, and call `execute(ctx)` |

Both call sites resolve **the same** preset capability refs, so selecting a
capability under **Others → Slicing Pipeline Plugin** wires up every step,
including `psGCodePostProcess` at export. The string `post_process_plugin`
does not appear anywhere in either document.

Wave Overhangs 0.0.2 assumed a second "Post-processing plugin" field existed
and gated carving on it; that was wrong and is fixed in 0.0.3.
