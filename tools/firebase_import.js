/**
 * Firebase Firestore Import Script
 * 
 * Imports inventory data to Firestore using Firebase Admin SDK
 * 
 * Prerequisites:
 *   1. npm install firebase-admin
 *   2. Set GOOGLE_APPLICATION_CREDENTIALS environment variable
 *      OR ensure gcloud is authenticated
 * 
 * Usage:
 *   node tools/firebase_import.js
 */

const admin = require('firebase-admin');
const fs = require('fs');
const path = require('path');

// Initialize Firebase Admin
// Uses application default credentials (gcloud auth)
admin.initializeApp({
  projectId: 'sapphire-479610'
});

const db = admin.firestore();
const COLLECTION = 'inventory';

async function importInventory() {
  // Read the import data
  const dataPath = path.join(__dirname, '..', 'data', 'firestore_inventory_import.json');
  const rawData = fs.readFileSync(dataPath, 'utf8');
  const inventory = JSON.parse(rawData);
  
  const docIds = Object.keys(inventory);
  console.log(`Found ${docIds.length} inventory items to import\n`);
  
  let imported = 0;
  let updated = 0;
  let errors = [];
  
  for (const docId of docIds) {
    try {
      const data = inventory[docId];
      const docRef = db.collection(COLLECTION).doc(docId);
      
      // Check if document exists
      const existing = await docRef.get();
      
      if (existing.exists) {
        await docRef.update(data);
        console.log(`✓ Updated: ${data.model_name}`);
        updated++;
      } else {
        await docRef.set(data);
        console.log(`✓ Created: ${data.model_name}`);
        imported++;
      }
    } catch (error) {
      console.error(`✗ Error importing ${docId}:`, error.message);
      errors.push({ id: docId, error: error.message });
    }
  }
  
  console.log('\n' + '='.repeat(60));
  console.log('IMPORT SUMMARY');
  console.log('='.repeat(60));
  console.log(`Created: ${imported}`);
  console.log(`Updated: ${updated}`);
  console.log(`Errors:  ${errors.length}`);
  console.log('='.repeat(60));
  
  if (errors.length > 0) {
    console.log('\nErrors encountered:');
    errors.forEach(e => console.log(`  - ${e.id}: ${e.error}`));
  }
  
  process.exit(errors.length > 0 ? 1 : 0);
}

// Run the import
importInventory().catch(error => {
  console.error('Import failed:', error);
  process.exit(1);
});
