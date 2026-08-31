/** Transport collapse copy is not gameserver detail (network-collapse densify). */
export function isTradingNetworkCollapseMessage(msg: string): boolean {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
}
