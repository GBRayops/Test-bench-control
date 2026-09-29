import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider
import matplotlib.patches as patches
from scipy.signal import savgol_filter

# Ouverture du fichier CSV 
file_path = r""
df = pd.read_csv(file_path, sep=';', skiprows = 6, header=None, dtype=str)

#Formattage du fichier 
df = df.replace(to_replace=[r'","', r'",', r',"'], value=';', regex=True)
df = df.replace(to_replace=[r','], value='.', regex=True)
df = df.replace(to_replace=[r';'], value=',', regex=True)
new_df = df[0].str.split(',', expand=True)
new_df = new_df.iloc[:, :-1]
df_clean = new_df[pd.to_numeric(new_df[0], errors='coerce').notnull()]


n_rows = df_clean.shape[0]
n_cols = df_clean.shape[1]


#Nombre d'images enregistrées (256 lignes par image)
n_frames = n_rows / 256

#Matrice de température 3D (n_frames, 256, n_cols)
temp_array = np.zeros((int(n_frames), 256, n_cols))

for frames in range(int(n_frames)):
    start_row = frames * 256
    end_row = start_row + 256
    frame_data = df_clean.iloc[start_row:end_row, :]
    temp_array[frames, :, :] = frame_data.to_numpy(dtype=float)



#Aluminium properties
rho = 2700  # kg/m^3
cp = 900  # J/kg/K
l = 0.007  # épaisseur (m) semelle = 3,00mm + boite = 4,00mm

#Copper properties
#rho = 8960  # kg/m^3
#cp = 385  # J/kg/K
#l = 0.007  # épaisseur (m)

px = 1.667e-3  # taille estimée d'un pixel sur l'objet à 50cm (m)


#Calcul de l'énergie thermique totale (J) pour chaque image
# E = rho * cp * V * delta_T

energy = rho*cp*l*px*px*(temp_array[:, :, :] - temp_array[0, :, :])  # J

#Création de la figure et des axes pour les sous-graphes
fig, ax = plt.subplots(figsize=(10, 15), nrows=2, ncols=2)
ax[0][0].sharex(ax[1][0]) 
ax[0][0].sharey(ax[1][0]) 
ax[0][1].sharex(ax[1][1]) 


fig.suptitle(f'{file_path.split("\\")[-2]} {file_path.split("\\")[-1][:-27]}')
plt.subplots_adjust(top=0.94,
bottom=0.18,
left=0.0,
right=0.935,
hspace=0.255,
wspace=0.4)

point_of_measurement = (132, 150)  # Coordonnées du point P (row, column) ou (y, x)

im = ax[0][0].imshow(temp_array[0, :, :], cmap='jet', vmax = np.max(temp_array), vmin = np.min(temp_array))

#Afficher les points de mesure P et C sur la carte de température
ax[0][0].scatter(point_of_measurement[1], point_of_measurement[0], color='white', marker='o', facecolor = 'none', s=50, label='P')
ax[0][0].scatter(temp_array.shape[2]//2, temp_array.shape[1]//2, color='white', marker='o', facecolor = 'none', s=50, label='C')
ax[0][0].text(point_of_measurement[1]+5, point_of_measurement[0], 'P', color='white', fontsize=12, fontweight='bold')
ax[0][0].text(temp_array.shape[2]//2+5, temp_array.shape[1]//2, 'C', color='white', fontsize=12, fontweight='bold')


#Création du slider pour naviguer dans les images (temps)
ax_slider = plt.axes([0.25, 0.1, 0.65, 0.03])
slider = Slider(ax_slider, 'Temps (s)', 0, (temp_array.shape[0] - 1)*0.2, valinit=0, valfmt='%d')



ax[0][0].set_title("Carte de température")
fig.colorbar(im, ax=ax[0][0], label="Température (°C)", fraction=0.05, pad=0.04)

#Distribution 2D d'énergie thermique
im_energy = ax[1][0].imshow(energy[0, :, :], cmap='hot', vmax = np.max(energy), vmin = np.min(energy))
ax[1][0].set_title("Carte d'énergie")
fig.colorbar(im_energy, ax=ax[1][0], label="Énergie (J)", fraction=0.05, pad=0.04)

#Définition de la région d'intérêt (ROI) pour l'analyse de l'énergie thermique totale
start_row = 100
start_col = 10 
width = 160
height = 60
rect = patches.Rectangle(
    (start_col, start_row), 
    width, 
    height, 
    linewidth=2,       # Épaisseur du contour du rectangle
    edgecolor='g',     # Couleur du contour du rectangle
    facecolor='none'   # 'none' pour garder le rectangle transparent
)
ax[1][0].add_patch(rect)
ax[1][0].text(start_row-5,start_col + width//2-10, 'ROI', color='white', fontsize=12, fontweight='bold')

#Graph de l'énergie totale vs temps
total_energy = np.sum(energy[:, start_row:start_row+height, start_col:start_col+width], axis=(1, 2))
t = np.arange(len(total_energy))*0.2 #5fps = 200ms/frame

ax[1][1].plot(t, total_energy, color='orange')
ax[1][1].set_title("Énergie totale dans la ROI (J)")
ax[1][1].set_xlabel("Temps (s)")
ax[1][1].set_ylabel("Énergie (J)")

#Graph de Température maximale vs temps
vline = ax[0][1].axvline(x=0, color='red', linestyle='--', linewidth=2)
ax[0][1].plot(t, temp_array[:, point_of_measurement[0], point_of_measurement[1]], label="Température à P", linestyle='--')
ax[0][1].plot(t, temp_array[:, temp_array.shape[1]//2, temp_array.shape[2]//2], label="Température à C", linestyle='--')
ax[0][1].plot(t, np.max(temp_array, axis=(1,2)), label="Température maximale (°C)")
ax[0][1].set_title("Température maximale (°C)")
ax[0][1].set_xlabel("Temps (s)")
ax[0][1].legend()

# Update function for the slider
def update(val):
    slice_idx = int(slider.val)
    im.set_data(temp_array[int(slice_idx*5), :, :]) # Update the image data
    im_energy.set_data(energy[int(slice_idx*5), :, :]) # Update the energy distribution image data
    vline.set_xdata([slice_idx, slice_idx])
    fig.canvas.draw_idle()
    

e_max = np.max(total_energy)
t_E_max = t[np.argmax(total_energy)]
smooth_total_energy = savgol_filter(total_energy, window_length=11, polyorder=3)  # Smooth the total energy data
dt = t[1] - t[0]  
gradient = np.gradient(smooth_total_energy, dt)  # Calculate the derivative of the smoothed total energy

start_slope = gradient[:50]
gradient = gradient > start_slope.mean() + 2*start_slope.std()

N = 10

start_idx = None

for i in range(len(gradient) - N):
    if np.all(gradient[i:i+N] > 0):
        start_idx = i
        break
t_start = t[start_idx]
ax[1][1].plot(t, smooth_total_energy, color='blue', label="Énergie totale lissée (J)")





heat_power = (e_max - total_energy[start_idx])/(t_E_max - t_start)  # W


try:
    print(f"Temps de départ: {t_start:.2f} s")
    print(f"Temps d'énergie maximale: {t_E_max:.2f} s")
    print(f"Temps ON: {t_E_max - t_start:.2f} s")
    print(f"Énergie maximale: {e_max:.2f} J")
    print(f"Énergie totale au temps de départ: {total_energy[start_idx]:.2f} J")
    print(f"Puissance thermique total: {heat_power:.2f} W")
except:
    print("Erreur dans le calcul de la puissance thermique totale. Analyse manuelle nécessaire.")
    pass

slider.on_changed(update)
plt.show()



