program produce_ic_mesh
   implicit none

   ! --- FILES ---
   character*200 :: outputfile   = 'TEST_MESH'
   character*200 :: outcontrol   = 'probe3D_mesh.txt'
   character*200 :: input_points = 'particles.dat'

   ! --- GADGET HEADERS ---
   integer*4 npart(0:5), nall(0:5)
   real*8    massarr(0:5)
   real*8    a, redshift
   integer*4 unused(34)
   
   ! --- VARIABLES ---
   integer*4 i, zeile
   integer*4 total_particles
   real*4    spacing_abstand
   real*4    density_standard
   
   ! --- DYNAMIC ARRAYS ---
   real*4,allocatable    :: pos(:,:), vel(:,:), masses(:), u(:)
   integer*4,allocatable :: id(:), object(:), g_type(:), plate(:), hinge(:)
   
   integer :: io_status
   
   ! --- TEMP VARIABLES FOR READING ---
   real*4    :: x_in, y_in, z_in
   integer*4 :: obj, g, wall  ! New variable to hold the Object ID from file

   ! --- USER CONFIGURATION ---
   spacing_abstand = 0.0025  ! 2.5mm spacing
   density_standard = 1000.0 
   ! --------------------------

   print *, '-----------------------------------------'
   print *, 'GADGET-2 IC GENERATOR (MULTI-OBJECT MODE)'
   print *, '-----------------------------------------'

   ! 1. CHECK IF INPUT EXISTS
   open(10, file=input_points, status='old', action='read', iostat=io_status)
   if (io_status /= 0) then
       print *, 'CRITICAL ERROR: Could not open ', trim(input_points)
       print *, 'Make sure you ran the Python script first!'
       stop
   end if

   ! 2. COUNT PARTICLES
   print *, 'Reading particles.dat to count lines...'
   total_particles = 0
   do
       ! MODIFIED: Added id_in_temp to match the 4 columns in the file
       read(10, *, iostat=io_status) x_in, y_in, z_in, obj, g, wall
       if (io_status /= 0) exit
       total_particles = total_particles + 1
   end do
   close(10)

   if (total_particles == 0) then
       print *, 'CRITICAL ERROR: particles.dat is empty.'
       stop
   end if

   print *, 'Particle count:', total_particles

   ! 3. SETUP HEADERS
   npart = 0
   npart(0) = total_particles 
   nall = npart
   massarr = 0
   a = 0
   redshift = 0
   unused = 0

   ! 4. ALLOCATE MEMORY
   allocate(pos(3, total_particles))
   allocate(vel(3, total_particles))
   allocate(masses(total_particles))
   allocate(u(total_particles))
   allocate(id(total_particles))
   allocate(object(total_particles)) ! This will now store real Object IDs
   allocate(g_type(total_particles))
   allocate(plate(total_particles))
   allocate(hinge(total_particles))

   ! 5. READ DATA (PASS 2)
   print *, 'Importing data and assigning IDs...'
   open(10, file=input_points, status='old', action='read')
   
   do i = 1, total_particles
       ! MODIFIED: Read X, Y, Z AND the Object ID
       read(10, *) pos(1,i), pos(2,i), pos(3,i), obj, g, wall
       
       ! Standard Properties
       vel(1,i) = 0.0
       vel(2,i) = 0.0
       vel(3,i) = 0.0
       masses(i) = (spacing_abstand**3) * density_standard
       u(i) = 1.0e-6
       id(i) = wall
       
       ! MODIFIED: Assign the actual Object ID from the file
       object(i) = obj
       
       ! Default flags
       g_type(i) = g
       plate(i)  = 0
       hinge(i)  = 0
   end do
   close(10)

   ! 6. WRITE BINARY OUTPUT (GADGET FORMAT)
   print *, 'Writing binary output to: ', trim(outputfile)
   
   open (1, file=outputfile, form = 'unformatted', status='replace', iostat=io_status) 
   if (io_status /= 0) then
       print *, 'CRITICAL ERROR: Could not create output file.'
       stop
   end if

   write (1) npart, massarr, a, redshift, 0, 0, nall, unused
   write (1) pos
   write (1) vel
   write (1) id
   write (1) masses
   write (1) u
   write (1) object   ! Now contains correct IDs (1, 2, 3...)
   write (1) g_type
   write (1) plate
   write (1) hinge
   close (1)
   
   print *, 'SUCCESS: ', trim(outputfile), ' created.'

   ! 7. WRITE CONTROL FILE (TEXT)
   ! This helps you verify the IDs were read correctly without opening the binary
   open (2, file=outcontrol, status='replace')
   write (2, *) '# ID  X  Y  Z  Object_ID'
   do zeile=1, total_particles
       write (2, *) zeile, pos(1:3, zeile), object(zeile)
   end do
   close (2)

   deallocate(pos, vel, masses, u, id, object, g_type, plate, hinge)

end program produce_ic_mesh
