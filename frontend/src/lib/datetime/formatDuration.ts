/**
 * An appointment's length, from its own start and end strings (both carry the
 * salon's offset, so the difference is the real length even across a DST
 * change): 90 minutes -> "1 год 30 хв", 60 -> "1 год", 45 -> "45 хв". Same
 * wording as the booking emails (core/formatting.py `format_duration`).
 */
export function formatAppointmentDuration(startIso: string, endIso: string): string {
  const minutes = Math.round((new Date(endIso).getTime() - new Date(startIso).getTime()) / 60000);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const parts: string[] = [];
  if (hours > 0) {
    parts.push(`${hours} год`);
  }
  if (rest > 0 || hours === 0) {
    parts.push(`${rest} хв`);
  }
  return parts.join(" ");
}
