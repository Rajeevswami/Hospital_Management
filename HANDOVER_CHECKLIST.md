# Hospital Staff Handover Checklist

When handing the system over to a hospital, provide/explain all of the following:

## 1. Hand over login credentials
- [ ] Admin login (username + password) — this is their main account
- [ ] Create Doctor/Receptionist/Pharmacist accounts from the Admin panel (Staff section)
- [ ] **Tell them to change the password after the first login**

## 2. Initial setup (one-time)
- [ ] Add Wards and Beds from Django Admin (`/admin/`) (General Ward, ICU, etc. + bed numbers)
- [ ] Add Doctors from the "Doctors" section (login + specialization + fee)
- [ ] Add Medicines from the "Pharmacy" section (enter stock quantities carefully)

## 3. Daily use training (show this to the staff)
- [ ] **Receptionist**: registering a new patient, booking an appointment, admitting a patient
- [ ] **Doctor**: viewing their appointments, creating a prescription
- [ ] **Pharmacist**: updating medicine stock, viewing low-stock alerts
- [ ] **Admin/Receptionist**: creating an invoice, recording a payment, downloading the PDF bill

## 4. Important notes for the hospital
- Patient IDs are generated automatically (PAT-2026-0001 format) — do not create them manually
- Invoice numbers are automatic too (INV-2026-0001 format)
- Once an appointment is discharged/completed it never appears in billing again (double-billing is impossible)
- The bed is freed automatically on discharge

## 5. Data safety
- The database is backed up automatically every night at 2 AM (retained for 14 days)
- Backup files can be found at: `/var/backups/hospital_system/`
- If important data is deleted accidentally, it can be restored from a backup (contact the developer)

## 6. Support
- If a bug or issue occurs, take a screenshot of the error message and send it to the developer
- If the server seems "down", first check whether the domain opens at all, and try from another device

## 7. Future scope (if needed later)
- SMS/WhatsApp appointment reminders
- Lab reports module
- Multi-branch support (if the hospital has other branches)
