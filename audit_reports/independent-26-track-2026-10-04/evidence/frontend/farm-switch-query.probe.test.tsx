import { expect, it } from 'vitest';
import { permittedAppPathFromList } from '@/lib/permission-navigation';
it('CONFIRMED: cross-farm destinations retain old-farm IDs encoded in root-route query parameters', () => {
 const permissions=['health.view','health.manage','breeding.view','breeding.manage','kidding.view','kidding.manage'];
 for(const path of ['/screening?image_id=71','/health?task_id=12&animal_id=81','/breeding?ultrasound_id=91','/kidding?breeding_id=101']) {
   expect(permittedAppPathFromList(path,permissions)).toBe(path);
 }
 expect(permittedAppPathFromList('/health/schedule/81',permissions)).toBe('/health');
});
