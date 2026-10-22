/** Isolated so tests can replace it; jsdom cannot navigate. */
export function navigateTo(url: string): void {
  window.location.assign(url);
}
