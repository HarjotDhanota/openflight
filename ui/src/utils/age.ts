/**
 * How old a reading is, in words.
 *
 * Lives here rather than beside the component so the component file exports
 * only components, which is what keeps fast refresh working.
 */
export function formatAge(ageS: number | null): string | null {
  if (ageS === null) return null;
  if (ageS < 30) return 'just now';
  if (ageS < 90) return 'a minute ago';
  return `${Math.round(ageS / 60)} min ago`;
}
