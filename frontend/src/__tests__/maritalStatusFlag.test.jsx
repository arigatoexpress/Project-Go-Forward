import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

// PART B item #6 (Mark Willcott markup, 2026-06-24): Marital Status is a
// Married/Unmarried dropdown (already shipped), and Mark asked whether the field
// is still needed. This pins (a) the dropdown options, and (b) a visible
// humanFlag note surfacing Mark's open question to staff, plus the SelectField
// support for helperText that renders it.

const SRC = fs.readFileSync(
  path.resolve(__dirname, '../pages/DocumentCenter.jsx'),
  'utf8',
);

describe('Marital Status (PART B #6)', () => {
  it('is a Married/Unmarried dropdown (idiot-proof, per Mark)', () => {
    expect(SRC).toMatch(/MARITAL_STATUS_OPTIONS\s*=\s*\[/);
    expect(SRC).toContain("label: 'Married'");
    expect(SRC).toContain("label: 'Unmarried'");
  });

  it('carries a humanFlag asking whether the field is still needed', () => {
    // The note must be attached to the buyer marital status field.
    const block = SRC.slice(
      SRC.indexOf('name="buyer_marital_status"'),
      SRC.indexOf('name="buyer_marital_status"') + 400,
    );
    expect(block).toMatch(/helperText=/);
    expect(block.toLowerCase()).toMatch(/still need this/);
    expect(block.toLowerCase()).toContain('mark');
  });

  it('SelectField renders helperText so the flag is visible to staff', () => {
    // SelectField must accept + render a helperText prop (data-testid helper-<name>).
    const selStart = SRC.indexOf('function SelectField(');
    const selBlock = SRC.slice(selStart, selStart + 1200);
    expect(selBlock).toMatch(/helperText/);
    expect(selBlock).toMatch(/data-testid=\{`helper-\$\{name\}`\}/);
  });
});
