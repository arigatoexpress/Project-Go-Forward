import React from 'react';
import { Phone } from 'lucide-react';
import ContentPage from '../components/ContentPage';
import { BUSINESS_NAME, BUSINESS_PHONE, BUSINESS_PHONE_RAW, BUSINESS_CITY, BUSINESS_STATE } from '../constants';
import { FINANCING_LINE } from '../seoCopy';

const Financing = ({ onBack }) => (
  <ContentPage
    title={`Manufactured Home Financing in ${BUSINESS_CITY}, ${BUSINESS_STATE}`}
    subtitle={`Talk with ${BUSINESS_NAME} about ways to pay for a home.`}
    onBack={onBack}
  >
    <div className="space-y-8 text-gray-700 leading-relaxed">
      <section>
        <p className="text-lg">{FINANCING_LINE}</p>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3">What a lender may ask for</h2>
        <ul className="list-disc pl-6 space-y-2">
          <li>Government-issued ID</li>
          <li>Proof of income, such as pay stubs, tax returns, or benefit letters</li>
          <li>Bank statements</li>
          <li>Land lease agreement or property information, if you have it</li>
        </ul>
        <p className="mt-3 text-sm">
          Exact requirements vary by lender. Financing paperwork is handled in person
          or through a secure lender portal. We do not collect sensitive documents in chat.
        </p>
      </section>

      <section className="bg-blue-900 text-white rounded-xl p-6">
        <h2 className="text-lg font-bold mb-2">Talk with our team</h2>
        <p className="mb-4 text-blue-100">
          Ask our team about financing options. The fastest next step is a phone call
          or a showroom visit.
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <a
            href={`tel:${BUSINESS_PHONE_RAW}`}
            className="inline-flex items-center gap-2 bg-white text-blue-900 px-5 py-2.5 rounded-lg font-semibold hover:bg-blue-50 transition"
          >
            <Phone size={18} />
            {BUSINESS_PHONE}
          </a>
          <a
            href="/appointments"
            className="inline-flex items-center gap-2 border border-white text-white px-5 py-2.5 rounded-lg font-semibold hover:bg-white/10 transition"
          >
            Book a Showroom Visit
          </a>
        </div>
      </section>
    </div>
  </ContentPage>
);

export default Financing;
