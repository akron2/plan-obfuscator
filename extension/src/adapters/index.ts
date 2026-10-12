import { ChatGptAdapter } from "./chatgpt";
import { ClaudeAdapter } from "./claude";
import type { SiteAdapter } from "./site-adapter";

const adapters: SiteAdapter[] = [new ChatGptAdapter(), new ClaudeAdapter()];

export function adapterFor(url: URL): SiteAdapter | null {
  return adapters.find((adapter) => adapter.matches(url)) ?? null;
}

export { ChatGptAdapter, ClaudeAdapter };
export type { SiteAdapter };
