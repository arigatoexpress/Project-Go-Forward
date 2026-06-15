import React from 'react';
import { ShieldCheck, Wrench, FileText, Phone, Clock, AlertTriangle } from 'lucide-react';
import ContentPage from '../components/ContentPage';
import { BUSINESS_NAME, BUSINESS_PHONE, BUSINESS_PHONE_RAW } from '../constants';

const Warranty = ({ onBack }) => (
  <ContentPage
    title="Warranty & Service"
    subtitle={`How we stand behind the homes we sell at ${BUSINESS_NAME}.`}
    onBack={onBack}
  >
    <div className="space-y-8 text-gray-700 leading-relaxed">
      <section>
        <p className="text-lg">
          Every new manufactured home we sell is backed by warranties designed to protect your
          investment. Below is what you can typically expect; we'll provide the exact warranty
          documents for the specific home and manufacturer you choose.
        </p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-gray-50 rounded-xl p-6 border border-gray-100">
          <div className="flex items-center gap-2 mb-3">
            <ShieldCheck size={22} className="text-blue-600" />
            <h3 className="text-lg font-bold text-gray-900">Manufacturer's Warranty</h3>
          </div>
          <p>
            New manufactured homes include a manufacturer's warranty covering the structure and major
            systems. Warranty length and terms vary by manufacturer and series; we'll review them
            with you before you buy.
          </p>
        </div>

        <div className="bg-gray-50 rounded-xl p-6 border border-gray-100">
          <div className="flex items-center gap-2 mb-3">
            <Wrench size={22} className="text-blue-600" />
            <h3 className="text-lg font-bold text-gray-900">Component Warranties</h3>
          </div>
          <p>
            Appliances, HVAC, roofing materials, and other components often carry their own
            manufacturer warranties. We help you understand what's covered by whom and how to file a
            claim if needed.
          </p>
        </div>

        <div className="bg-gray-50 rounded-xl p-6 border border-gray-100">
          <div className="flex items-center gap-2 mb-3">
            <FileText size={22} className="text-blue-600" />
            <h3 className="text-lg font-bold text-gray-900">Texas TDHCA Oversight</h3>
          </div>
          <p>
            Manufactured housing in Texas is regulated by the Texas Department of Housing and
            Community Affairs (TDHCA). This means your home must meet state installation and safety
            requirements, and you have a regulatory body to escalate serious issues if necessary.
          </p>
        </div>

        <div className="bg-gray-50 rounded-xl p-6 border border-gray-100">
          <div className="flex items-center gap-2 mb-3">
            <Clock size={22} className="text-blue-600" />
            <h3 className="text-lg font-bold text-gray-900">Service Timeline</h3>
          </div>
          <p>
            Warranty service requests are typically handled by the home manufacturer or an authorized
            service provider. We help coordinate the request, provide your documentation, and make
            sure you know what to expect at each step.
          </p>
        </div>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3 flex items-center gap-2">
          <AlertTriangle size={22} className="text-amber-600" />
          Safety First
        </h2>
        <p>
          If you smell gas, see electrical sparking or burning, have an active fire, or your
          carbon monoxide alarm is sounding, get everyone out of the home and call <strong>911</strong>
          or your gas/utility provider first. Warranty paperwork can wait until everyone is safe.
        </p>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3">How to Request Service</h2>
        <ol className="list-decimal pl-6 space-y-2">
          <li>
            <strong>Gather your info:</strong> your name, phone, home serial number (if known),
            purchase date, and a description of the issue.
          </li>
          <li>
            <strong>Call or message us</strong> at {BUSINESS_PHONE} so we can confirm warranty
            coverage and next steps.
          </li>
          <li>
            <strong>Document the issue</strong> with photos if it's safe to do so — this helps the
            manufacturer or service technician respond faster.
          </li>
          <li>
            <strong>We'll coordinate</strong> the warranty claim or service appointment and keep you
            updated until it's resolved.
          </li>
        </ol>
      </section>

      <section className="bg-blue-900 text-white rounded-xl p-6">
        <h2 className="text-lg font-bold mb-2 flex items-center gap-2">
          <Phone size={20} />
          Need Service Now?
        </h2>
        <p className="mb-4 text-blue-100">
          For warranty questions or service coordination, call our team directly.
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
