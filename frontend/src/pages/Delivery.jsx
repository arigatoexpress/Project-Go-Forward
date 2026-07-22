import React from 'react';
import { Truck, MapPin, HardHat, CalendarCheck, Phone, ClipboardList } from 'lucide-react';
import ContentPage from '../components/ContentPage';
import {
  BUSINESS_NAME,
  BUSINESS_PHONE,
  BUSINESS_PHONE_RAW,
  BUSINESS_FULL_ADDRESS,
  BUSINESS_HOURS,
} from '../constants';

const Delivery = ({ onBack }) => (
  <ContentPage
    title="Delivery & Setup"
    subtitle={`What to expect when your new home arrives from ${BUSINESS_NAME}.`}
    onBack={onBack}
  >
    <div className="space-y-8 text-gray-700 leading-relaxed">
      <section>
        <p className="text-lg">
          Buying the home is just the start. {BUSINESS_NAME} coordinates delivery, placement, and
          setup so your home is ready to live in. Below is the typical process — we'll confirm the
          exact details for your site and home before delivery day.
        </p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-gray-50 rounded-xl p-6 border border-gray-100">
          <div className="flex items-center gap-2 mb-3">
            <Truck size={22} className="text-blue-600" />
            <h3 className="text-lg font-bold text-gray-900">Transport & Placement</h3>
          </div>
          <p>
            We arrange transport from the factory or our lot to your prepared site. Once on site, the
            home is placed, leveled, and — for multi-section homes — the sections are joined and
            sealed.
          </p>
        </div>

        <div className="bg-gray-50 rounded-xl p-6 border border-gray-100">
          <div className="flex items-center gap-2 mb-3">
            <HardHat size={22} className="text-blue-600" />
            <h3 className="text-lg font-bold text-gray-900">Site Prep (Buyer-Side)</h3>
          </div>
          <p>
            Site prep — including the pad or piers, footings, utility runs, drive/access path, and
            permits — is usually arranged separately by the buyer or their contractor. We'll tell you
            exactly what needs to be ready before delivery can be scheduled.
          </p>
        </div>

        <div className="bg-gray-50 rounded-xl p-6 border border-gray-100">
          <div className="flex items-center gap-2 mb-3">
            <ClipboardList size={22} className="text-blue-600" />
            <h3 className="text-lg font-bold text-gray-900">Utility Connections</h3>
          </div>
          <p>
            We connect the home to on-site utilities (water, sewer/septic, electric, gas) according
            to local code. Some utility hookups may require a licensed local plumber or electrician —
            we'll clarify this during site review.
          </p>
        </div>

        <div className="bg-gray-50 rounded-xl p-6 border border-gray-100">
          <div className="flex items-center gap-2 mb-3">
            <CalendarCheck size={22} className="text-blue-600" />
            <h3 className="text-lg font-bold text-gray-900">Final Walkthrough</h3>
          </div>
          <p>
            Before we hand over the keys, we do a final walkthrough with you to confirm everything
            is level, connected, and operating correctly, and to answer any questions about your new
            home.
          </p>
        </div>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3">Service Area</h2>
        <p>
          We're located at <strong>{BUSINESS_FULL_ADDRESS}</strong> and deliver throughout the
          greater Houston area, including:
        </p>
        <p className="mt-2 font-medium text-gray-900">
          Huffman · Humble · Atascocita · Crosby · Kingwood · New Caney · Baytown · Beaumont ·
          Cleveland · Conroe · Livingston
        </p>
        <p className="mt-3">
          Delivery beyond this radius may be possible depending on route, home size, and site
          accessibility. Call us to confirm delivery to your address.
        </p>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3">Typical Timeline</h2>
        <ul className="list-disc pl-6 space-y-2">
          <li>
            <strong>Confirmed on-lot home:</strong> delivery can often be scheduled within weeks once
            availability, financing, and site prep are complete.
          </li>
          <li>
            <strong>Factory-ordered floor plan:</strong> manufacturing + transport lead times vary by
            builder and season. We'll give you a realistic estimate when you order.
          </li>
          <li>
            <strong>Site prep:</strong> usually the longest variable. Starting pad/utility work early
            prevents delays after the home arrives.
          </li>
        </ul>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3">Delivery Day Checklist</h2>
        <ul className="list-disc pl-6 space-y-2">
          <li>Site is cleared, graded, and accessible for a wide-load truck</li>
          <li>Pad/piers/footings are complete and inspected, if required</li>
          <li>Utilities are stubbed or ready for connection</li>
          <li>Any required permits are approved and posted</li>
          <li>Someone is available to meet the delivery crew and do the walkthrough</li>
        </ul>
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
