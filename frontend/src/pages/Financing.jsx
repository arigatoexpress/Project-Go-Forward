import React from 'react';
import { DollarSign, Home, LandPlot, FileText, Phone, CheckCircle } from 'lucide-react';
import ContentPage from '../components/ContentPage';
import { BUSINESS_NAME, BUSINESS_PHONE, BUSINESS_PHONE_RAW, BUSINESS_CITY, BUSINESS_STATE } from '../constants';

const Financing = ({ onBack }) => (
  <ContentPage
    title="Financing Options"
    subtitle={`Affordable paths to home ownership at ${BUSINESS_NAME}.`}
    onBack={onBack}
  >
    <div className="space-y-8 text-gray-700 leading-relaxed">
      <section>
        <p className="text-lg">
          Buying a manufactured home does not have to be complicated. At {BUSINESS_NAME}
          in {BUSINESS_CITY}, {BUSINESS_STATE}, we work with buyers across a range of
          credit profiles and land situations to find the financing option that fits.
        </p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-gray-50 rounded-xl p-6 border border-gray-100">
          <div className="flex items-center gap-2 mb-3">
            <Home size={22} className="text-blue-600" />
            <h3 className="text-lg font-bold text-gray-900">Chattel (Home-Only) Loan</h3>
          </div>
          <p>
            Financing for the home itself when it will sit on leased land, in a
            manufactured-home community, or on family property. Chattel loans typically
            close faster and require less paperwork than traditional mortgages.
          </p>
        </div>

        <div className="bg-gray-50 rounded-xl p-6 border border-gray-100">
          <div className="flex items-center gap-2 mb-3">
            <LandPlot size={22} className="text-blue-600" />
            <h3 className="text-lg font-bold text-gray-900">Land & Home Loan</h3>
          </div>
          <p>
            If you own or are buying land together with the home, a land-and-home loan
            can bundle both into one mortgage-style loan. This option may qualify for
            government-backed programs.
          </p>
        </div>

        <div className="bg-gray-50 rounded-xl p-6 border border-gray-100">
          <div className="flex items-center gap-2 mb-3">
            <FileText size={22} className="text-blue-600" />
            <h3 className="text-lg font-bold text-gray-900">FHA / VA / USDA Programs</h3>
          </div>
          <p>
            Depending on your situation, you may qualify for FHA Title I or Title II,
            VA, or USDA Rural Development programs. These can offer competitive down
            payments and terms for eligible buyers.
          </p>
        </div>

        <div className="bg-gray-50 rounded-xl p-6 border border-gray-100">
          <div className="flex items-center gap-2 mb-3">
            <DollarSign size={22} className="text-blue-600" />
            <h3 className="text-lg font-bold text-gray-900">In-House & Partner Programs</h3>
          </div>
          <p>
            We maintain relationships with manufactured-home lenders and in-house
            programs designed for a range of credit profiles. Ask us which partners
            currently serve your scenario.
          </p>
        </div>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3">What Affects Your Payment</h2>
        <ul className="list-disc pl-6 space-y-2">
          <li>Home size, series, and factory options</li>
          <li>Down payment and credit history</li>
          <li>Whether you own land, lease land, or need a community referral</li>
          <li>Delivery distance and site-prep requirements</li>
          <li>Current manufacturer incentives and lender rates</li>
        </ul>
      </section>

      <section>
        <h2 className="text-xl font-bold text-gray-900 mb-3">What to Bring</h2>
        <ul className="list-disc pl-6 space-y-2">
          <li>Government-issued ID</li>
          <li>Proof of income (pay stubs, tax returns, or benefit letters)</li>
          <li>Bank statements</li>
          <li>Land lease agreement or property information, if applicable</li>
        </ul>
        <p className="mt-3 text-sm">
          Exact requirements vary by lender. We never ask for sensitive documents in
          chat — financing paperwork is handled in person or through a secure lender
          portal.
        </p>
      </section>

      <section className="bg-blue-900 text-white rounded-xl p-6">
        <h2 className="text-lg font-bold mb-2 flex items-center gap-2">
          <CheckCircle size={20} />
          Ready to Explore Your Options?
        </h2>
        <p className="mb-4 text-blue-100">
          Every buyer's situation is different. The fastest way to understand your
          monthly payment and next steps is to talk with our team.
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
