"""CLI text formatting for headline lists."""

from __future__ import annotations

def format_lines(items, with_links):
    out = []
    for text, href in items:
        if with_links and href:
            out.append(f"{text}\n  {href}")
        else:
            out.append(text)
    return out


def format_site_output(results, new_results, seen_results, is_first_run, no_cache_mode, with_links):
    if no_cache_mode or is_first_run:
        lines = format_lines(results, with_links)
        output_text = "\n".join(lines)
        if is_first_run:
            note = (
                f"({len(results)} headlines found - first run, nothing to compare against yet; "
                f"future runs will show a 'new' section)"
            )
        else:
            note = f"({len(results)} headlines found)"
    else:
        sections = []
        if new_results:
            sections.append(
                f"=== New since last run ({len(new_results)}) ===\n"
                + "\n".join(format_lines(new_results, with_links))
            )
        else:
            sections.append("=== New since last run (0) ===\nNone")
        if seen_results:
            sections.append(
                f"=== Previously seen ({len(seen_results)}) ===\n"
                + "\n".join(format_lines(seen_results, with_links))
            )
        output_text = "\n\n".join(sections)
        note = f"({len(results)} headlines found, {len(new_results)} new)"
    return output_text, note
