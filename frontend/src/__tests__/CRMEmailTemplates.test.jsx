import { describe, expect, it } from 'vitest';

import { BUSINESS_FULL_ADDRESS, BUSINESS_PHONE } from '../constants';
import { EMAIL_TEMPLATES, renderEmailTemplate } from '../pages/CRM';

describe('CRM email templates', () => {
  it('renders every template without unresolved placeholders or fake contact details', () => {
    for (const template of EMAIL_TEMPLATES) {
      const rendered = renderEmailTemplate(template.id);

      expect(rendered).not.toBeNull();
      expect(`${rendered.subject}\n${rendered.message}`).not.toMatch(/{{[^}]+}}/);
      expect(rendered.message).not.toContain('(210) 555-0123');
      expect(rendered.message).not.toContain('123 Main Street');
      expect(rendered.message).not.toContain('San Antonio, TX 78201');
      expect(rendered.message).not.toMatch(/^Hi\b/im);
      expect(rendered.message).not.toMatch(/(?:best|warm) regards/im);
      expect(rendered.message).not.toContain(BUSINESS_PHONE);
    }
  });

  it('uses the canonical Huffman showroom address without pretending to know the appointment time', () => {
    const appointment = renderEmailTemplate('appointment_confirmation');

    expect(appointment.message).toContain(BUSINESS_FULL_ADDRESS);
    expect(appointment.message).not.toMatch(/appointment details|confirmed/i);
  });

  it('returns null for an unknown template', () => {
    expect(renderEmailTemplate('not-a-template', 'Maria')).toBeNull();
  });
});
