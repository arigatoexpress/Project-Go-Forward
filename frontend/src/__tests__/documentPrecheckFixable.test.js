import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import fieldMap from '../../../config/field_map.json';
import { getSelectedTemplateRequiredState } from '../pages/DocumentCenter';

// Regression guard for the 2026-11-23 outage: the Document Center pre-check
// demanded "manufacturer model home id" for every Full New/Used Home closing.
// That key is computed and has no input, so staff could not clear the error.
//
// These tests do not depend on a hand-written sample deal. They read the
// inputs the wizard actually renders and require, for every packet and every
// template in config/field_map.json:
//   1. every field the pre-check flags as missing is an input staff can fill;
//   2. no error message shows a raw snake_case key;
//   3. once every rendered input is filled, the pre-check passes.

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(here, '../pages/DocumentCenter.jsx'), 'utf8');

const RENDERED_INPUTS = new Set(
  [...source.matchAll(/<(?:Field|ComboField|SelectField)\b[^>]*?\bname="([a-z0-9_]+)"/g)].map(m => m[1]),
);

const valueFor = name => {
  if (/date/.test(name)) return '2026-01-15';
  if (/(^|_)state$/.test(name)) return 'TX';
  if (/zip$/.test(name)) return '77301';
  if (/phone/.test(name)) return '281-555-0100';
  if (/email/.test(name)) return 'staff-check@example.com';
  if (/ssn/.test(name)) return '111-22-3333';
  if (/no_of_sections/.test(name)) return '1';
  if (/(price|payment|amount|premium|cost|fee|rate|apr|income|width|length|years|months|term|value)/.test(name)) return '1000';
  return `Test ${name.replace(/_/g, ' ')}`;
};

const FILLED_FORM = Object.fromEntries([...RENDERED_INPUTS].map(name => [name, valueFor(name)]));

const templatesFor = names => names.map(name => ({
  template_name: name,
  display_name: fieldMap.templates[name]?.display_name || name,
  required_fields: fieldMap.templates[name]?.required_fields || [],
}));

const packetCases = Object.entries(fieldMap.packets).map(([name, packet]) => [`packet ${name}`, packet.templates]);
const templateCases = Object.keys(fieldMap.templates)
  .filter(name => (fieldMap.templates[name].required_fields || []).length > 0)
  .map(name => [`template ${name}`, [name]]);

it('finds the wizard inputs it checks against', () => {
  // If the Field markup changes shape, fail loudly instead of passing vacuously.
  expect(RENDERED_INPUTS.size).toBeGreaterThan(50);
  ['buyer_first_name', 'manufacturer', 'model', 'serial_number_1', 'buyer_county'].forEach(name => {
    expect(RENDERED_INPUTS.has(name)).toBe(true);
  });
});

it('every packet lists only templates that exist in the field map', () => {
  Object.entries(fieldMap.packets).forEach(([packet, { templates }]) => {
    const unknown = templates.filter(name => !fieldMap.templates[name]);
    expect({ packet, unknown }).toEqual({ packet, unknown: [] });
  });
});

describe.each([...packetCases, ...templateCases])('%s', (_label, names) => {
  const templates = templatesFor(names);

  it('only flags inputs staff can fill', () => {
    const state = getSelectedTemplateRequiredState({}, templates, names);
    const unfixable = [...state.missing].filter(field => !RENDERED_INPUTS.has(field));
    expect(unfixable).toEqual([]);
  });

  it('never shows a raw field key in an error', () => {
    const state = getSelectedTemplateRequiredState({}, templates, names);
    state.errors.forEach(message => {
      expect(message).not.toMatch(/\b[a-z0-9]+(?:_[a-z0-9]+)+\b/);
    });
  });

  it('passes once every rendered input is filled', () => {
    const state = getSelectedTemplateRequiredState(FILLED_FORM, templates, names);
    expect(state.errors).toEqual([]);
  });
});
