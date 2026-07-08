import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

// PART B item #6 (Mark Willcott markup, confirmed 2026-07-08): Marital Status
// stays on the form as a simple Married/Unmarried dropdown.

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

  it('does not carry the old open-question helper note', () => {
    const block = SRC.slice(
      SRC.indexOf('name="buyer_marital_status"'),
      SRC.indexOf('name="buyer_marital_status"') + 400,
    );
    expect(block).not.toMatch(/helperText=/);
    expect(block.toLowerCase()).not.toMatch(/still need this/);
  });
});
