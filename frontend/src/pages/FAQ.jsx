import React from 'react';
import { HelpCircle, Phone, Calendar, MessageCircle } from 'lucide-react';
import ContentPage from '../components/ContentPage';
import {
  BUSINESS_NAME,
  BUSINESS_PHONE,
  BUSINESS_PHONE_RAW,
  BUSINESS_FULL_ADDRESS,
  BUSINESS_HOURS,
} from '../constants';

const FAQ_ITEMS = [
  {
    question: "What's the difference between a manufactured home, a mobile home, and a modular home?",
    answer: (
      <>
        "Mobile home" is the older term for factory-built homes made <strong>before June 15, 1976</strong>.
        Homes built after that date to the federal <strong>HUD Code</strong> are called
        <strong> manufactured homes</strong> — they meet national standards for construction, strength,
        energy efficiency, and fire safety. <strong>Modular homes</strong> are also factory-built but
        assembled to local/state building codes (like a site-built home). {BUSINESS_NAME} sells new
        <strong> manufactured homes</strong> in single- and multi-section floor plans.
      </>
    ),
  },
  {
    question: 'Why do the listings say "Call for Price"?',
    answer: (
      <>
        Manufactured-home pricing depends on options, delivery distance, site prep, and current
        factory promotions, so we give you a real, itemized quote rather than a misleading sticker
        number. <a href={`tel:${BUSINESS_PHONE_RAW}`} className="text-blue-700 hover:underline font-semibold">Call {BUSINESS_PHONE}</a> or
        request a quote on any home and we'll walk you through the all-in cost.
      </>
    ),
  },
  {
    question: 'How does financing work for a manufactured home?',
    answer: (
      <>
        Several options are commonly available depending on whether you own or are buying land:
        <ul className="list-disc pl-6 mt-2 space-y-1">
          <li>
            <strong>Chattel (home-only) loans</strong> — for the home itself when it's on leased land or in a community.
          </li>
          <li>
            <strong>Land-and-home / mortgage loans</strong> — when the home and land are financed together; may qualify
            for FHA Title I/II, VA, or USDA programs.
          </li>
          <li>
            <strong>In-house / lender-partner programs</strong> for a range of credit profiles.
          </li>
        </ul>
        <p className="mt-2">
          We'll help you compare monthly payment, down payment, and term. Financing paperwork is
          handled in person or through a secure lender portal — never over chat.
        </p>
      </>
    ),
  },
  {
    question: 'Do I need land to buy a home from you?',
    answer: (
      <>
        No. You can place a home on <strong>land you own</strong>, on <strong>family land</strong>, or in a
        <strong> manufactured-home community / leased lot</strong>. We can talk through the trade-offs
        of each and what site prep each requires.
      </>
    ),
  },
  {
    question: 'What does delivery and setup include?',
    answer: (
      <>
        A typical new-home delivery includes transport from the factory/lot, placement and leveling on
        your prepared site, joining multi-section homes, and connecting to utilities per local code.
        Site prep (pad, footings/piers, utility runs, permits) is usually arranged separately. We
        coordinate the schedule and walk you through each step.
      </>
    ),
  },
  {
    question: 'What areas do you serve / deliver to?',
    answer: (
      <>
        We're located at <strong>{BUSINESS_FULL_ADDRESS}</strong>, and serve the greater Houston area —
        including <strong>Huffman, Humble, Atascocita, Crosby, Kingwood, New Caney, Baytown, Beaumont,
        Cleveland, Conroe, and Livingston</strong>. Call us to confirm delivery options for your specific
        address.
      </>
    ),
  },
  {
    question: 'What warranty comes with a new manufactured home?',
    answer: (
      <>
        New manufactured homes carry a <strong>manufacturer's warranty</strong> on the home and its major
        systems, and many components (appliances, HVAC, roofing) carry their own manufacturer
        warranties. In Texas, manufactured housing is regulated by the <strong>Texas Department of Housing
        and Community Affairs (TDHCA)</strong>. We'll provide the specific warranty documents for the home
        you choose.
      </>
    ),
  },
  {
    question: 'How long does the whole process take?',
    answer: (
      <>
        For an <strong>on-lot home that's ready now</strong>, it can be weeks once financing and site prep are
        in place. For an <strong>ordered (factory) floor plan</strong>, build + delivery lead times vary by
        manufacturer and season. We'll give you a realistic timeline up front.
      </>
    ),
  },
  {
    question: 'Can I tour homes in person?',
    answer: (
      <>
        Yes — visit our Huffman showroom/lot during business hours ({BUSINESS_HOURS}), or
        <a href="/appointments" className="text-blue-700 hover:underline font-semibold"> book a showroom visit</a> online
        and we'll have a home finder ready for you. Many listings also include photos and 3D virtual
        tours you can explore from home.
      </>
    ),
  },
  {
    question: 'How do I get started?',
    answer: (
      <>
        Three easy ways: <a href={`tel:${BUSINESS_PHONE_RAW}`} className="text-blue-700 hover:underline font-semibold">call or text {BUSINESS_PHONE}</a>,
        <a href="/inventory" className="text-blue-700 hover:underline font-semibold"> request a price quote</a> on any home,
        or <a href="/appointments" className="text-blue-700 hover:underline font-semibold">book a showroom visit</a>.
        Tell us your budget, whether you have land, and the size you need, and we'll match you to the
        right homes.
      </>
    ),
  },
];

const FAQ = ({ onBack }) => (
  <ContentPage
    title="Frequently Asked Questions"
    subtitle={`Everything you need to know about buying a manufactured home with ${BUSINESS_NAME}.`}
    onBack={onBack}
  >
    <div className="space-y-8">
      {FAQ_ITEMS.map((item, index) => (
        <section key={index} className="border-b border-gray-100 last:border-0 pb-6 last:pb-0">
          <h2 className="text-lg font-bold text-gray-900 mb-3 flex items-start gap-3">
            <HelpCircle size={22} className="text-blue-600 shrink-0 mt-0.5" />
            <span>{item.question}</span>
          </h2>
          <div className="pl-9 text-gray-700 leading-relaxed space-y-2">
            {item.answer}
          </div>
        </section>
      ))}

      <section className="bg-blue-50 rounded-xl p-6 border border-blue-100 mt-8">
        <h2 className="text-lg font-bold text-gray-900 mb-2">Still have questions?</h2>
        <p className="text-gray-700 mb-4">
          Our team is happy to help. Call, text, book a visit, or start a chat.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <a
            href={`tel:${BUSINESS_PHONE_RAW}`}
            className="inline-flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg font-medium hover:bg-blue-700 transition"
          >
            <Phone size={18} />
            {BUSINESS_PHONE}
          </a>
          <a
            href="/appointments"
            className="inline-flex items-center gap-2 border border-blue-600 text-blue-700 px-4 py-2 rounded-lg font-medium hover:bg-blue-50 transition"
          >
            <Calendar size={18} />
            Book a Visit
          </a>
          <a
            href="/chat"
            className="inline-flex items-center gap-2 border border-blue-600 text-blue-700 px-4 py-2 rounded-lg font-medium hover:bg-blue-50 transition"
          >
            <MessageCircle size={18} />
            Chat with Tex
          </a>
        </div>
      </section>
    </div>
  </ContentPage>
);

export default FAQ;
