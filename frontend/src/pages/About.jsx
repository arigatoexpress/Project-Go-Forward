import React from 'react';
import { MapPin, Phone, ShieldCheck, Users, Home } from 'lucide-react';
import ContentPage from '../components/ContentPage';
import {
  BUSINESS_NAME,
  BUSINESS_LEGAL_NAME,
  BUSINESS_PHONE,
  BUSINESS_PHONE_RAW,
  BUSINESS_FULL_ADDRESS,
  BUSINESS_HOURS,
  BUSINESS_LICENSE,
  BUSINESS_CITY,
  BUSINESS_STATE,
} from '../constants';

const About = ({ onBack }) => (
  <ContentPage
    title="About Us"
    subtitle={`Your local manufactured home specialist in ${BUSINESS_CITY}, ${BUSINESS_STATE}.`}
    onBack={onBack}
  >
    <div className="space-y-8 text-gray-700 leading-relaxed">
      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3 flex items-center gap-2">
          <Home size={22} className="text-blue-600" />
          Who We Are
        </h2>
        <p>
          {BUSINESS_NAME} is a family-focused manufactured and mobile home dealership
          headquartered in {BUSINESS_CITY}, {BUSINESS_STATE}. We help Texans find quality
          factory-built homes — from budget-friendly pre-owned units to brand-new
          single-section and multi-section floorplans — without the pressure of a
          traditional sales floor.
        </p>
        <p className="mt-3">
          Operating as {BUSINESS_LEGAL_NAME}, we combine on-lot inventory you can tour
          today with orderable factory floorplans for buyers who want a home built to
          their specifications.
        </p>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3 flex items-center gap-2">
          <Users size={22} className="text-blue-600" />
          Our Approach
        </h2>
        <ul className="list-disc pl-6 space-y-2">
          <li>
            <strong>Honest, itemized quotes.</strong> Manufactured-home pricing depends
            on options, delivery distance, site prep, and current factory incentives. We
            walk you through the real numbers instead of hiding behind a sticker price.
          </li>
          <li>
            <strong>Local expertise.</strong> We know the Houston-area communities,
            land-lease options, and financing paths that matter to buyers in Southeast
            Texas.
          </li>
          <li>
            <strong>Showroom-first, low-pressure service.</strong> Tour homes in person,
            compare floorplans side-by-side, and ask questions before you make any
            commitment.
          </li>
        </ul>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3 flex items-center gap-2">
          <ShieldCheck size={22} className="text-blue-600" />
          Licensed & Regulated
        </h2>
        <p>
          {BUSINESS_NAME} is a licensed Texas retailer (License #{BUSINESS_LICENSE}).
          Manufactured housing in Texas is regulated by the Texas Department of Housing
          and Community Affairs (TDHCA), and every new home we sell meets or exceeds
          federal HUD Code construction and safety standards.
        </p>
      </section>

      <section className="bg-blue-50 rounded-xl p-6 border border-blue-100">
        <h2 className="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
          <MapPin size={20} className="text-blue-600" />
          Visit Our Showroom
        </h2>
        <p className="mb-2">{BUSINESS_FULL_ADDRESS}</p>
        <p className="mb-4 text-sm">Hours: {BUSINESS_HOURS}</p>
        <a
          href={`tel:${BUSINESS_PHONE_RAW}`}
          className="inline-flex items-center gap-2 text-blue-700 font-semibold hover:underline"
        >
          <Phone size={18} />
          {BUSINESS_PHONE}
        </a>
      </section>
    </div>
  </ContentPage>
);

export default About;
