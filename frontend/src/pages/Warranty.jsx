import React from 'react';
import { Phone, AlertTriangle } from 'lucide-react';
import ContentPage from '../components/ContentPage';
import { BUSINESS_NAME, BUSINESS_PHONE, BUSINESS_PHONE_RAW } from '../constants';
import { WARRANTY_LINE } from '../seoCopy';

const Warranty = ({ onBack }) => (
  <ContentPage
    title="Warranty & Service"
    subtitle={WARRANTY_LINE}
    onBack={onBack}
  >
    <div className="space-y-8 text-gray-700 leading-relaxed">
      <section>
        <p className="text-lg">
          {WARRANTY_LINE} {BUSINESS_NAME} can walk through the paperwork for the
          home you are considering.
        </p>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3 flex items-center gap-2">
          <AlertTriangle size={22} className="text-amber-600" />
          Safety First
        </h2>
        <p>
          If you smell gas, see electrical sparking or burning, have an active fire, or your
          carbon monoxide alarm is sounding, get everyone out of the home and call <strong>911</strong>
          or your gas or utility provider first. Warranty paperwork can wait until everyone is safe.
        </p>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3">How to ask about service</h2>
        <ol className="list-decimal pl-6 space-y-2">
          <li>
            <strong>Gather your info:</strong> your name, phone, home serial number if you know it,
            purchase date, and a description of the issue.
          </li>
          <li>
            <strong>Call us</strong> at {BUSINESS_PHONE}. {WARRANTY_LINE}
          </li>
          <li>
            <strong>Photos help</strong> when it is safe to take them. Share them with the team
            when you call.
          </li>
        </ol>
      </section>

      <section className="bg-blue-900 text-white rounded-xl p-6">
        <h2 className="text-lg font-bold mb-2 flex items-center gap-2">
          <Phone size={20} />
          Need Service Now?
        </h2>
        <p className="mb-4 text-blue-100">
          For warranty questions, call our team and ask a manager.
        </p>
        <a
          href={`tel:${BUSINESS_PHONE_RAW}`}
          className="inline-flex items-center gap-2 bg-white text-blue-900 px-5 py-2.5 rounded-lg font-semibold hover:bg-blue-50 transition"
        >
          {BUSINESS_PHONE}
        </a>
      </section>
    </div>
  </ContentPage>
);

export default Warranty;
