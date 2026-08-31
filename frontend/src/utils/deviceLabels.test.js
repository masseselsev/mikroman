import { describe, expect, it } from 'vitest';
import { displayVendor } from './deviceLabels';

/**
 * The vendor line kept overflowing because it printed a fact the row already
 * showed as a badge. These pin the strings the backend actually stores - taken
 * from vendor_lookup.py and from a live database - rather than invented ones.
 */
describe('displayVendor', () => {
  it('drops the randomization marker, which the PRIVATE badge already carries', () => {
    // Half of this string is the duplicate that pushed the vendor off the line.
    expect(displayVendor('Google Pixel (Private MAC)')).toBe('Google Pixel');
    expect(displayVendor('Apple (Private MAC)')).toBe('Apple');
    expect(displayVendor('Samsung (Private MAC)')).toBe('Samsung');
    expect(displayVendor('Xiaomi (Private MAC)')).toBe('Xiaomi');
  });

  it('returns nothing when the marker is all there is', () => {
    // No hardware identity to show, so the field is omitted rather than
    // repeating the badge.
    expect(displayVendor('Private MAC (Randomized)')).toBeNull();
    expect(displayVendor('Private MAC')).toBeNull();
  });

  it('leaves a real vendor untouched', () => {
    expect(displayVendor('Quanta Computer')).toBe('Quanta Computer');
    expect(displayVendor('MikroTik')).toBe('MikroTik');
    expect(displayVendor('AzureWave')).toBe('AzureWave');
  });

  it('is not case-sensitive about the marker', () => {
    expect(displayVendor('Apple (private mac)')).toBe('Apple');
    expect(displayVendor('Apple (PRIVATE MAC)')).toBe('Apple');
  });

  it('only strips the marker at the end, not a name that contains it', () => {
    expect(displayVendor('Private MAC Systems Ltd')).toBe('Private MAC Systems Ltd');
  });

  it('handles missing input', () => {
    expect(displayVendor(null)).toBeNull();
    expect(displayVendor(undefined)).toBeNull();
    expect(displayVendor('')).toBeNull();
    expect(displayVendor('   ')).toBeNull();
  });

  it('meaningfully shortens the strings that were overflowing', () => {
    // The row has roughly 30 characters for "IP · vendor" on one line.
    const before = 'Google Pixel (Private MAC)';
    expect(before).toHaveLength(26);
    expect(displayVendor(before).length).toBeLessThanOrEqual(13);
  });
});
