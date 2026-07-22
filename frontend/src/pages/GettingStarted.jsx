import React, { useState } from 'react';
import {
  BookOpen, FileText, Users, Home, Video, CheckCircle2,
  ArrowRight, Search, ShieldCheck, ClipboardList, HelpCircle, Camera
} from 'lucide-react';

const WORKFLOW_STEPS = [
  {
    title: 'Start with the customer',
    body: 'Create or load the customer record in Document Center. Save the record as soon as the buyer name and contact details are known.',
    icon: Users,
  },
  {
    title: 'Choose the exact home',
    body: 'Select inventory when possible so model, manufacturer, price, sections, factory details, wind zone, weights, and dates can fill from the record.',
    icon: Home,
  },
  {
    title: 'Confirm installer and site',
    body: 'Texas Home Outlet is selected as installer by default. Switch to another installer when a deal requires a different licensed installer.',
    icon: ShieldCheck,
  },
  {
    title: 'Generate the packet',
    body: 'Pick a recommended packet or individual forms, review missing-field warnings, then generate and download the merged PDFs.',
    icon: FileText,
  },
];

const WIKI_SECTIONS = {
  daily: {
    label: 'Daily Flow',
    icon: ClipboardList,
    items: [
      'Use CRM for leads, tasks, appointments, and customer follow-up.',
      'Use Documents for deal paperwork, contract packets, and recent PDF downloads.',
      'Use Inventory to distinguish listed homes from orderable manufacturer floorplans, then confirm current availability before sending quotes or forms.',
      'Use Ad Studio only for reviewed public-facing inventory or approved campaign drafts.',
    ],
  },
  docs: {
    label: 'Documents',
    icon: FileText,
    items: [
      'Customer Info catches duplicate deals before you start new paperwork.',
      'Choose Home fills every available inventory field and clearly labels anything that still needs manual entry.',
      'Installer defaults to Texas Home Outlet and can be switched to another installer without retyping the rest of the deal.',
      'Review & Generate blocks packet generation until required buyer and home fields are present.',
    ],
  },
  data: {
    label: 'Data Quality',
    icon: Search,
    items: [
      'Listed Home means a catalog listing whose availability must be confirmed; Orderable Floorplan means a manufacturer plan customers can ask THO to custom order.',
      'New public floorplans should come from manufacturer media, the approved THO Drive folder, or the reviewed inventory package before publishing.',
      'Actual lot photos should replace stock or floorplan-only images whenever staff has dealer photos for that home.',
      'Catalog photos are not the same as actual lot photos; approved dealer photos should be reviewed before publishing.',
      'Serial numbers, labels, weights, wind zone, date of manufacture, and manufacturer address should be verified when the inventory feed is blank.',
      'Customer SSNs are masked in browser draft storage and should not be pasted into notes.',
      'Fresh Drive or Notion content should enter a review queue before it changes production inventory.',
    ],
  },
  photos: {
    label: 'Adding Photos',
    icon: Camera,
    items: [
      'Open the Photos tab in the top menu (you must be logged in with the PIN or a passkey).',
      'Step 1 — pick the home: type part of its name, then click it in the list.',
      'Step 2 — add pictures: click the dashed box to choose photos, or drag them in. You can add several at once.',
      'Step 3 — review: the first photo (marked "Main") is what customers see first; click "Make main" on any photo to feature it.',
      'Tick "Show only homes that still need photos" to see exactly which homes are missing pictures.',
      'Photos are auto-rotated and shrunk for the web, and appear on the public Inventory page within about a minute.',
      'Full step-by-step with pictures: docs/team/photo-upload-guide.md.',
    ],
  },
  support: {
    label: 'Support',
    icon: HelpCircle,
    items: [
      'If a form looks wrong, check the highlighted missing fields first.',
      'If a generated PDF is missing data, confirm the field exists in the Document Center form and the source inventory record.',
      'If the app asks for admin access, unlock with PIN once and then register a passkey on trusted devices.',
      'Future Notion pages can link here as the staff handbook once the workflows are final.',
    ],
  },
};

export default function GettingStarted({ onOpenDocuments, onOpenCRM, onOpenAdStudio }) {
  const [active, setActive] = useState('daily');
  const ActiveIcon = WIKI_SECTIONS[active].icon;

  return (
    <main className="min-h-screen bg-[var(--cp-bg)] text-[var(--cp-text)]">
      <section className="border-b border-[var(--cp-border)] bg-[var(--cp-panel)]">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-6">
            <div className="max-w-3xl">
              <div className="inline-flex items-center gap-2 rounded-full bg-[var(--cp-accent-dim)] text-[var(--cp-accent)] px-3 py-1 text-xs font-bold uppercase tracking-wide">
                <BookOpen size={14} />
                Staff Guide
              </div>
              <h1 className="mt-4 text-3xl sm:text-4xl font-bold tracking-tight">
                Getting Started With THO
              </h1>
              <p className="mt-3 text-[var(--cp-muted)] text-base leading-7">
                A practical operating guide for Texas Home Outlet staff: customers, inventory, documents, installer details, and reviewed public content.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={onOpenDocuments} className="cp-btn-accent px-4 py-2 text-sm inline-flex items-center gap-2">
                <FileText size={16} />
                Open Documents
              </button>
              <button type="button" onClick={onOpenCRM} className="cp-btn-outline px-4 py-2 text-sm inline-flex items-center gap-2">
                <Users size={16} />
                Open CRM
              </button>
              <button type="button" onClick={onOpenAdStudio} className="cp-btn-outline px-4 py-2 text-sm inline-flex items-center gap-2">
                <Video size={16} />
                Open Ad Studio
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {WORKFLOW_STEPS.map((step, index) => {
            const Icon = step.icon;
            return (
              <article key={step.title} className="cp-panel p-5">
                <div className="flex items-center justify-between">
                  <div className="w-10 h-10 rounded-lg bg-[var(--cp-accent-dim)] text-[var(--cp-accent)] flex items-center justify-center">
                    <Icon size={20} />
                  </div>
                  <span className="text-xs font-bold text-[var(--cp-faint)]">Step {index + 1}</span>
                </div>
                <h2 className="mt-4 text-lg font-bold">{step.title}</h2>
                <p className="mt-2 text-sm text-[var(--cp-muted)] leading-6">{step.body}</p>
              </article>
            );
          })}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6">
          <nav className="cp-panel p-3 h-fit" aria-label="Guide sections">
            {Object.entries(WIKI_SECTIONS).map(([key, section]) => {
              const Icon = section.icon;
              const selected = active === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setActive(key)}
                  className={`w-full flex items-center justify-between gap-3 rounded-md px-3 py-3 text-left text-sm font-semibold transition ${
                    selected
                      ? 'bg-[var(--cp-accent)] text-[var(--cp-bg)]'
                      : 'text-[var(--cp-text)] hover:bg-[var(--cp-surface)]'
                  }`}
                >
                  <span className="inline-flex items-center gap-2">
                    <Icon size={16} />
                    {section.label}
                  </span>
                  <ArrowRight size={14} />
                </button>
              );
            })}
          </nav>

          <article className="cp-panel p-6">
            <div className="flex items-start gap-3">
              <div className="w-11 h-11 rounded-lg bg-[var(--cp-secondary-dim)] text-[var(--cp-secondary)] flex items-center justify-center">
                <ActiveIcon size={22} />
              </div>
              <div>
                <h2 className="text-2xl font-bold">{WIKI_SECTIONS[active].label}</h2>
                <p className="text-sm text-[var(--cp-muted)] mt-1">Keep this checklist open while training or validating a new workflow.</p>
              </div>
            </div>
            <div className="mt-6 grid grid-cols-1 md:grid-cols-2 gap-3">
              {WIKI_SECTIONS[active].items.map(item => (
                <div key={item} className="rounded-lg border border-[var(--cp-border)] bg-[var(--cp-bg-2)] p-4 flex gap-3">
                  <CheckCircle2 size={18} className="text-[var(--cp-cta-green)] shrink-0 mt-0.5" />
                  <p className="text-sm leading-6 text-[var(--cp-text-secondary)]">{item}</p>
                </div>
              ))}
            </div>
          </article>
        </div>
      </section>
    </main>
  );
}
