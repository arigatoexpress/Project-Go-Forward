import { describe, it, expect } from 'vitest';
import fieldMap from '../../../config/field_map.json';
import {
  getSelectedTemplateRequiredState,
  manufacturerModelHomeId,
} from '../pages/DocumentCenter';

// Staff report 2026-11-23: Full New Home closings were blocked with
// "manufacturer model home id is required for Homestead Exemption Application".
// That field is computed (manufacturer + model + HUD label or serial); there is
// no input for it, so the Document Center must derive it, never ask for it.

const templatesFor = names => names.map(name => ({
  template_name: name,
  display_name: fieldMap.templates[name]?.display_name || name,
  required_fields: fieldMap.templates[name]?.required_fields || [],
}));

const COMPLETE_FORM = {
  buyer_first_name: 'Test',
  buyer_last_name: 'Buyer',
  buyer_address: '100 Main St',
  buyer_city: 'Conroe',
  buyer_state: 'TX',
  buyer_zip: '77301',
  buyer_county: 'Montgomery',
  manufacturer: 'TRU Homes',
  manufacturer_address: '1 Plant Rd',
  manufacturer_city: 'Bonham',
  manufacturer_state: 'TX',
  manufacturer_zip: '75418',
  model: 'The Marvel',
  serial_number_1: 'TRU0987654A',
  no_of_sections: '1',
  date_of_manufacture: '2026-10-01',
  sales_price: '89900',
};

describe('manufacturerModelHomeId', () => {
  it('uses HUD labels when entered', () => {
    expect(manufacturerModelHomeId({
      manufacturer: 'TRU Homes', model: 'The Marvel',
      label_number_1: 'NTA1', label_number_2: 'NTA2', serial_number_1: 'S1',
    })).toBe('TRU Homes The Marvel / NTA1, NTA2');
  });
  it('falls back to serials when no HUD label is entered', () => {
    expect(manufacturerModelHomeId({
      manufacturer: 'TRU Homes', model: 'The Marvel',
      serial_number_1: 'S1', serial_number_2: 'S2',
    })).toBe('TRU Homes The Marvel / S1, S2');
  });
  it('is blank without manufacturer/model or any ID', () => {
    expect(manufacturerModelHomeId({ serial_number_1: 'S1' })).toBe('');
    expect(manufacturerModelHomeId({ manufacturer: 'TRU Homes', model: 'X' })).toBe('');
  });
});

describe.each(['full_closing_new', 'full_closing_used'])('%s packet', packetName => {
  const names = fieldMap.packets[packetName].templates;
  const templates = templatesFor(names);

  it('is not blocked when the home has manufacturer, model and serial (no HUD label)', () => {
    const state = getSelectedTemplateRequiredState(COMPLETE_FORM, templates, names);
    expect(state.errors).toEqual([]);
  });

  it('points staff at real inputs, never the computed home-ID key', () => {
    const form = { ...COMPLETE_FORM, serial_number_1: '' };
    const state = getSelectedTemplateRequiredState(form, templates, names);
    expect(state.missing.has('manufacturer_model_home_id')).toBe(false);
    expect(state.missing.has('serial_number_1')).toBe(true);
    expect(state.errors.join(' ')).not.toMatch(/manufacturer model home id/i);
  });
});

it('every required field across all templates is derivable from the wizard form', () => {
  // Guard: a computed field added to required_fields must also be derived in
  // toDocumentData, or staff get a "<raw key> is required" error they cannot fix.
  const names = Object.keys(fieldMap.templates).filter(
    name => (fieldMap.templates[name].required_fields || []).length > 0,
  );
  const state = getSelectedTemplateRequiredState(COMPLETE_FORM, templatesFor(names), names);
  expect(state.errors).toEqual([]);
});
