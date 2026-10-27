import React from 'react';
import { MapPin, Phone, ShieldCheck } from 'lucide-react';
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
import { DELIVERY_LINE, FINANCING_LINE, WARRANTY_LINE } from '../seoCopy';

const About = ({ onBack }) => (
  <ContentPage
    title="About Us"
    subtitle={`Manufactured and mobile homes in ${BUSINESS_CITY}, ${BUSINESS_STATE}.`}
    onBack={onBack}
  >
    <div className="space-y-8 text-gray-700 leading-relaxed">
      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3">Who We Are</h2>
        <p>
          {BUSINESS_NAME} is a manufactured and mobile home dealership in {BUSINESS_CITY}, {BUSINESS_STATE}.
          The showroom is at {BUSINESS_FULL_ADDRESS}. Hours: {BUSINESS_HOURS}.
        </p>
        <p className="mt-3">
          The legal name is {BUSINESS_LEGAL_NAME}. The lot has listed homes you can tour
          and orderable floorplans you can ask the team about.
        </p>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3 flex items-center gap-2">
          <ShieldCheck size={22} className="text-blue-600" />
          Licensed Retailer
        </h2>
        <p>
          {BUSINESS_NAME} is a licensed Texas retailer (License #{BUSINESS_LICENSE}).
          Manufactured housing in Texas is regulated by the Texas Department of Housing
          and Community Affairs (TDHCA).
        </p>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3">What to ask our team</h2>
        <ul className="list-disc pl-6 space-y-2">
          <li>{FINANCING_LINE}</li>
          <li>{DELIVERY_LINE}</li>
          <li>{WARRANTY_LINE}</li>
        </ul>
        <p className="mt-3">
          Prices on this website stay Call for Price until you confirm the total with the team.
          The total depends on the options, delivery distance, and site setup for that home.
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
