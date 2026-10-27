const WEB_ACCESS_MARKER = "[[offer_web_access]]";

/** The Runtime normally consumes this control token. Hide it in older history
 * and while a provider is still streaming it, without changing ordinary prose. */
export function visibleAssistantText(text: string, streaming = false): string {
  let visible = text.split(WEB_ACCESS_MARKER).join("");
  if (streaming) {
    for (let length = WEB_ACCESS_MARKER.length - 1; length >= 2; length -= 1) {
      if (visible.endsWith(WEB_ACCESS_MARKER.slice(0, length))) {
        visible = visible.slice(0, -length);
        break;
      }
    }
  }
  return visible;
}
