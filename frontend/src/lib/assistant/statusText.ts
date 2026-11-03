/**
 * Working-indicator texts for the assistant's status events
 * (docs/DECISIONS.md § "Stage 17 block D", working indicator; § "Stage 17
 * sub-step order", 17.2a and 17.5a). The step key is the tool name the
 * backend is about to run. An unknown key has no text: the widget keeps its
 * pulsing dots and never shows the raw key.
 */

const STATUS_TEXTS = new Map<string, string>([
  ["list_services", "Дивлюся послуги…"],
  ["check_availability", "Перевіряю вільний час…"],
  ["get_specialist_info", "Дивлюся інформацію про майстрів…"],
  ["get_faq", "Шукаю відповідь…"],
  ["get_salon_info", "Уточнюю деталі салону…"],
  ["propose_booking", "Готую пропозицію…"],
]);

export function statusText(step: string): string | null {
  return STATUS_TEXTS.get(step) ?? null;
}
