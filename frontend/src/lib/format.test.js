import { describe, it, expect } from 'vitest';
import { formatDuration } from './format.js';

describe('formatDuration', () => {
  it('formats 0 seconds as 0:00:00', () => {
    expect(formatDuration(0)).toBe('0:00:00');
  });

  it('formats a value under a minute', () => {
    expect(formatDuration(45)).toBe('0:00:45');
  });

  it('formats a value under an hour', () => {
    expect(formatDuration(61)).toBe('0:01:01');
  });

  it('formats a value over an hour', () => {
    expect(formatDuration(3725)).toBe('1:02:05');
  });

  it('treats a nullish value as 0 seconds', () => {
    expect(formatDuration(undefined)).toBe('0:00:00');
    expect(formatDuration(null)).toBe('0:00:00');
  });
});
