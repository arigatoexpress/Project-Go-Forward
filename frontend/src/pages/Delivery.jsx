import React from 'react';
import { MapPin, Phone } from 'lucide-react';
import ContentPage from '../components/ContentPage';
import {
  BUSINESS_NAME,
  BUSINESS_PHONE,
  BUSINESS_PHONE_RAW,
  BUSINESS_FULL_ADDRESS,
  BUSINESS_HOURS,
  SERVICE_AREA_CITIES,
} from '../constants';
import { DELIVERY_LINE } from '../seoCopy';

const Delivery = ({ onBack }) => (
  <ContentPage
    title="Delivery & Setup"
    subtitle={DELIVERY_LINE}
    onBack={onBack}
  >
    <div className="space-y-8 text-gray-700 leading-relaxed">
      <section>
        <p className="text-lg">
          {DELIVERY_LINE} {BUSINESS_NAME} can talk through transport, placement, and
          site preparation for a specific address. Ask for the details that apply to
          your home and your lot.
        </p>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3">Town pages</h2>
        <p>
          The showroom is at <strong>{BUSINESS_FULL_ADDRESS}</strong>. This website
          has a town page for:
        </p>
        <p className="mt-2 font-medium text-gray-900">
          {SERVICE_AREA_CITIES.join(' · ')}
        </p>
        <p className="mt-3">{DELIVERY_LINE}</p>
      </section>

      <section className="bg-blue-50 rounded-xl p-6 border border-blue-100">
        <h2 className="text-lg font-bold text-gray-900 mb-3 flex items-center gap-2">
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

export default Delivery;
