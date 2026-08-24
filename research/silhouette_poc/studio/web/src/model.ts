import type { Point } from './types';

export function criteriaRoleLabel(role: string): string {
  return role === 'primary' ? 'PRIMARY — PHASE A' : 'COMPARISON ONLY';
}

export function overlayPath(points: Point[], close: boolean): string {
  if (points.length === 0) return '';
  const body = points.map(([x, y], index) => `${index === 0 ? 'M' : 'L'} ${x} ${y}`).join(' ');
  return close ? `${body} Z` : body;
}

export function rejectionTotal(rejections: Record<string, number>): number {
  return Object.values(rejections).reduce((total, value) => total + value, 0);
}

export function mm(value: number | null, digits = 2): string {
  return value == null ? '—' : `${value.toFixed(digits)} mm`;
}

export function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function label(value: string): string {
  return value.replace('poc_', '').replaceAll('_', ' ');
}
